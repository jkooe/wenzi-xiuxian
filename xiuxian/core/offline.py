"""离线挂机结算（后台挂机累计修为）。

设计要点
--------
1. **时间基准统一用 UTC epoch 秒**：不用本地时间字符串，避免时区/夏令时导致
   的时长计算错误（旧存档的 `saved_at` 是本地时间字符串，只作兼容回退）。
2. **修为沉淀到「待领池」pending_exp**：离线时长按速率算出修为后不直接塞进
   当前层（会被 add_exp 的需求上限截断而浪费），而是先进池子，玩家突破后
   还能继续领。池子总量无上限。
3. **幂等**：结算成功后立刻把 last_settled_at 推进到本次结算时刻，并递增
   settled_count；重复调用时 elapsed 必然小于宽限期，不会再发一次。
4. **边界**：首次登录无记录 / 时间回拨 / 异常偏移 / 超长离线，全部有明确处理。

结算公式
--------
    有效时长 = clamp(now - last, 0, MAX_SECONDS) - GRACE_SECONDS
    修为     = 有效时长(小时) × 本层需求 × OFFLINE_EXP_RATIO_PER_HOUR × 神通加成

离线速率取「打坐-休息循环」的平均效率（打坐消耗与休息恢复同速，占空比 50%），
即离线 ≈ 主动打坐的一半 —— 挂机永远比不上亲自操作，但绝不浪费时间。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

# ---------- 可配置参数（全部集中在此，便于调平衡） ----------
OFFLINE_EXP_RATIO_PER_HOUR = 0.0100   # 离线修为 = 本层需求 × 1.0% / 小时（v2 校准：= 主动打坐的 50%）
GRACE_SECONDS = 60.0                  # 宽限期：小于 1 分钟的间隙不结算（防刷/防抖动）
MIN_SETTLE_SECONDS = 60.0             # 最短结算间隔：低于此时长不产生收益
MAX_OFFLINE_SECONDS = 365 * 24 * 3600.0   # 单次结算上限：365 天（防数据异常/篡改）
# 待领池上限 = 当前境界本层需求的 100%（防离线攒修为跳级）；
# 超出部分按 PENDING_OVERFLOW_RATIO 自动转化为灵石（30% 保值回收，不白丢）
PENDING_CAP_RATIO = 1.0
PENDING_OVERFLOW_RATIO = 0.30
# 领取时若超过当前层需求，超出部分衰减为 30%（防止突破后「瞬间领爆下一层」）
CLAIM_OVERFLOW_RATIO = 0.30


@dataclass
class OfflineState:
    """离线挂机状态（存档字段，见 to_dict / from_dict）。"""

    last_settled_at: float | None = None    # UTC epoch 秒；None = 首次登录，无记录
    pending_exp: float = 0.0                # 待领修为池（上限 = 本层需求 100%）
    total_offline_exp: float = 0.0          # 累计离线修为（统计/展示）
    settled_count: int = 0                  # 结算次数（幂等与审计）
    last_duration: float = 0.0              # 上次结算的有效时长（秒，展示用）
    anomaly: str = ""                       # 上次异常说明（回拨 / 超长截断 / 无记录）
    overflow_converted: float = 0.0         # 累计溢出转灵石（防跳级回收统计）

    def to_dict(self) -> dict[str, Any]:
        return {
            "last_settled_at": self.last_settled_at,
            "pending_exp": round(self.pending_exp, 2),
            "total_offline_exp": round(self.total_offline_exp, 2),
            "settled_count": self.settled_count,
            "last_duration": round(self.last_duration, 2),
            "anomaly": self.anomaly,
            "overflow_converted": round(self.overflow_converted, 2),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "OfflineState":
        data = data or {}
        raw = data.get("last_settled_at")
        try:
            last = float(raw) if raw is not None else None
        except (TypeError, ValueError):
            last = None                      # 老存档/脏数据：视为无记录
        return cls(
            last_settled_at=last,
            pending_exp=float(data.get("pending_exp", 0.0) or 0.0),
            total_offline_exp=float(data.get("total_offline_exp", 0.0) or 0.0),
            settled_count=int(data.get("settled_count", 0) or 0),
            last_duration=float(data.get("last_duration", 0.0) or 0.0),
            anomaly=str(data.get("anomaly", "") or ""),
            overflow_converted=float(data.get("overflow_converted", 0.0) or 0.0),
        )


def _now_utc() -> float:
    """统一时间源（UTC epoch 秒）。测试可 monkeypatch 此函数以模拟时间流逝。"""
    return time.time()


def compute_offline_gain(
    state: OfflineState,
    need: float,
    now: float | None = None,
    ratio_per_hour: float = OFFLINE_EXP_RATIO_PER_HOUR,
    cultivation_bonus: float = 0.0,
) -> tuple[float, float, str]:
    """计算离线收益。返回 (修为, 有效时长秒, 异常说明)。不修改 state。

    - 首次登录（last_settled_at 为 None）：不结算，异常说明为「首次登录」
    - 时间回拨（now < last）：不结算，异常说明为「时间回拨」
    - 超长离线：截断到 MAX_OFFLINE_SECONDS，异常说明为「超长截断」
    """
    now = _now_utc() if now is None else now
    last = state.last_settled_at

    if last is None:
        return 0.0, 0.0, "首次登录"
    elapsed = now - last
    if elapsed < 0:
        # 系统时间回拨：宁可不发，也不能让玩家靠改表把时间倒回去重复刷
        return 0.0, 0.0, "时间回拨"
    if elapsed < MIN_SETTLE_SECONDS:
        return 0.0, 0.0, ""

    anomaly = ""
    if elapsed > MAX_OFFLINE_SECONDS:
        elapsed = MAX_OFFLINE_SECONDS
        anomaly = "超长截断"

    effective = max(0.0, elapsed - GRACE_SECONDS)
    if effective <= 0:
        return 0.0, 0.0, anomaly

    hours = effective / 3600.0
    gain = hours * need * ratio_per_hour * (1.0 + cultivation_bonus)
    return gain, effective, anomaly


def settle(state: OfflineState, need: float, now: float | None = None,
           ratio_per_hour: float = OFFLINE_EXP_RATIO_PER_HOUR,
           cultivation_bonus: float = 0.0) -> tuple[float, float, str]:
    """执行结算并推进状态（**幂等**：重复调用不会再产生收益）。

    返回 (本次修为, 有效时长秒, 异常说明)。
    无论是否产生收益，都会把 last_settled_at 推进到 now —— 这样重复调用时
    elapsed 归零，天然幂等；首次登录与异常场景同样完成初始化。

    v2 防跳级：待领池上限 = 本层需求 × PENDING_CAP_RATIO，超出的部分按
    PENDING_OVERFLOW_RATIO 自动转化为灵石（30% 保值回收）——离线攒修为
    最多攒满一层，不浪费也跳不了级。
    """
    now = _now_utc() if now is None else now
    gain, duration, anomaly = compute_offline_gain(
        state, need, now=now, ratio_per_hour=ratio_per_hour,
        cultivation_bonus=cultivation_bonus,
    )
    if gain > 0:
        state.pending_exp += gain
        state.total_offline_exp += gain
        state.settled_count += 1
        state.last_duration = duration
    # 待领池上限（防跳级）：超出部分按 30% 转化为灵石
    cap = max(0.0, need) * PENDING_CAP_RATIO
    if state.pending_exp > cap:
        overflow = state.pending_exp - cap
        state.pending_exp = cap
        state.overflow_converted += overflow * PENDING_OVERFLOW_RATIO
    if anomaly:
        state.anomaly = anomaly
    # 关键：推进结算基准时刻，保证幂等
    state.last_settled_at = now
    return gain, duration, anomaly


def claim(state: OfflineState, add_exp_fn, amount: float | None = None) -> float:
    """从待领池领取修为。返回实际到账数量（受 add_exp 的本层需求上限约束，余额留池）。

    这样离线攒下的修为不会因为「本层已满」被浪费：突破后还能继续领取。

    v2：领取后若超过当前层需求，超出部分按 CLAIM_OVERFLOW_RATIO 衰减为 30%
    （转化入灵石），防止突破后一次性把下一层领爆——离线收益只该是「辅助」，
    不该直接填满一整层。
    """
    if state.pending_exp <= 0:
        return 0.0
    want = state.pending_exp if amount is None else min(amount, state.pending_exp)
    got = float(add_exp_fn(want))
    # 只扣除真正到账的量，余额继续留在池中
    state.pending_exp = max(0.0, state.pending_exp - got)
    # 超出当前层需求的部分（add_exp 截断掉的）衰减为 30% 转灵石
    overflow = want - got
    if overflow > 1e-9 and state.pending_exp <= 1e-9:
        state.overflow_converted += overflow * CLAIM_OVERFLOW_RATIO
    return got

"""Truthful rendering for consequential tool outcomes.

Most functions here unconditionally replace (part of) the model's own reply
with a hand-composed string — a deliberate, narrow exception to letting the
model phrase things, reserved for cases where an earlier live failure showed
the model stating a price, a time slot, or a completion that the tool trace
did not actually support (financial/booking correctness, not phrasing).

ground_direct_datetime_response and ground_daycare_recommendation_response
are the lower-stakes exception: they trust the model's own wording whenever
it demonstrably states the correct verified value, and only fall back to a
canned sentence when it doesn't (wrong, or no checkable answer at all). That
"verify, don't rewrite" shape is the preferred default for anything new —
full unconditional replacement should stay reserved for genuine
financial/booking-correctness risk.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import datetime
from typing import Any

from app.tools.text_formatting import contains_chinese

# Only ever reached from _ground_direct_datetime_response's own trigger check
# below, so these stay local rather than shared/injected.
DIRECT_DATE_WORD_RE = re.compile(
    r"\b(?:today|tomorrow|week|monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
    r"hari\s+ini|esok|minggu|isnin|selasa|rabu|khamis|jumaat|sabtu|ahad)\b"
    r"|今天|今日|明天|后天|後天|星期[一二三四五六日天1-7]|周[一二三四五六日天1-7]|週[一二三四五六日天1-7]",
    re.IGNORECASE,
)
OPERATIONAL_DATE_RE = re.compile(
    r"\b(?:book|booking|reserve|appointment|available|availability|slot|cancel|reschedule)\b"
    r"|预约|預約|订位|訂位|空位|取消|改期|安排|有位",
    re.IGNORECASE,
)

_WEEKDAY_EN_ZH = {
    "Monday": "星期一", "Tuesday": "星期二", "Wednesday": "星期三",
    "Thursday": "星期四", "Friday": "星期五", "Saturday": "星期六",
    "Sunday": "星期日",
}


def _response_confirms_resolved_date(content: str, value: str, weekday: str) -> bool:
    """True only if the model's own text demonstrably states the verified
    date or weekday — not merely the absence of a wrong one. The system
    prompt already tells the model to reuse
    conversation_state.current_datetime_resolution verbatim, so a
    correctly-phrased answer (any language/format, as long as it contains
    the ISO date or the matching weekday name) is trusted as-is. Anything
    else — silence on the actual date, a different one, a vague
    non-answer — still gets the deterministic fallback below, so a direct
    date question is never left without a correct, checkable answer.
    """
    if value and value in content:
        return True
    chinese_weekday = _WEEKDAY_EN_ZH.get(weekday, "")
    return bool(weekday) and (weekday in content or (bool(chinese_weekday) and chinese_weekday in content))


def _latest_result(trace: list[dict], tool_names: set[str]) -> tuple[str, dict] | None:
    attempt = next(
        (item for item in reversed(trace) if item.get("tool") in tool_names),
        None,
    )
    if attempt is None:
        return None
    try:
        result = json.loads(attempt.get("result") or "{}")
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(result, dict):
        return None
    return str(attempt.get("tool") or ""), result


def ground_direct_datetime_response(
    response: Any,
    state: Any,
    user_message: str,
    *,
    is_staff_handoff_request: Callable[[str], bool],
):
    """Trust the model's own phrasing for a short, non-operational date
    question; only fall back to a deterministic sentence if its answer
    actually contradicts the verified resolution (wrong ISO date or
    wrong weekday name), never merely for phrasing/format/language.

    Operational messages keep the model's natural response, but their
    writes still use the same resolution through the date whitelist.
    """
    resolved = state.current_datetime_resolution
    text = str(user_message or "").strip()
    if (
        not isinstance(resolved, dict)
        or len(text) > 60
        or not DIRECT_DATE_WORD_RE.search(text)
        or OPERATIONAL_DATE_RE.search(text)
        or is_staff_handoff_request(text)
    ):
        return response
    value = resolved.get("date")
    date_range = resolved.get("date_range") or {}
    chinese = contains_chinese(text)
    if value:
        try:
            weekday = datetime.fromisoformat(value).strftime("%A")
        except ValueError:
            weekday = ""
        if _response_confirms_resolved_date(str(response.content or ""), value, weekday):
            return response
        chinese_weekday = _WEEKDAY_EN_ZH.get(weekday, weekday)
        content = (
            f"日期是 {value}（{chinese_weekday}）。"
            if chinese else f"The date is {value} ({weekday})."
        )
        return response.model_copy(update={"content": content})
    if date_range.get("start") and date_range.get("end"):
        content = str(response.content or "")
        confirms = date_range["start"] in content and date_range["end"] in content
        if confirms:
            return response
        content = (
            f"日期范围是 {date_range['start']} 至 {date_range['end']}。"
            if chinese else
            f"The date range is {date_range['start']} through {date_range['end']}."
        )
        return response.model_copy(update={"content": content})
    return response


def ground_latest_availability_response(
    response: Any,
    user_message: str,
    trace: list[dict],
    state: Any = None,
    *,
    is_repeat_booking_request: Callable[[str], bool],
):
    """Render choices from the latest availability result, never model prose.

    This is intentionally limited to turns whose last substantive tool is
    an availability read. A later booking/price/policy action keeps its own
    response, while a slot-selection turn gets a deterministic allow-list
    so an unavailable time cannot leak into customer-facing text.
    """
    latest = None
    for item in reversed(trace):
        tool_name = item.get("tool")
        if tool_name in {"resolve_datetime", "update_conversation_state"}:
            continue
        if tool_name not in {"check_availability", "check_availability_range"}:
            return response
        latest = item
        break
    if latest is None:
        return response

    raw_result = latest.get("result")
    try:
        result = json.loads(raw_result) if isinstance(raw_result, str) else raw_result
    except (TypeError, ValueError, json.JSONDecodeError):
        return response
    if not isinstance(result, dict):
        return response

    chinese = contains_chinese(str(user_message or ""))
    malay = bool(re.search(
        r"\b(?:saya|boleh|pukul|masa|tempah|ambil|hantar)\b",
        str(user_message or ""),
        re.IGNORECASE,
    ))

    def with_repeat_context(content: str) -> str:
        template = getattr(state, "repeat_booking_template", None) if state else None
        repeat_evidence_in_this_turn = is_repeat_booking_request(user_message) or any(
            item.get("tool") == "get_last_completed_booking" for item in trace
        )
        if (
            not repeat_evidence_in_this_turn
            or not template
            or not template.get("catalogue_validated")
        ):
            return content
        pet_name = template.get("pet_name") or "your pet"
        package_name = template.get("package_name")
        price = template.get("price")
        add_on = template.get("add_on")
        price_text = f"RM{float(price):g}" if price not in (None, "") else ""
        add_on_text = f" + {add_on}" if add_on else ""
        if chinese:
            summary = (
                f"已按 {pet_name} 上次的选择锁定当前仍可预约的"
                f"「{package_name}」{add_on_text}"
                f"{f'（{price_text}）' if price_text else ''}。"
            )
        else:
            summary = (
                f"I matched {pet_name}'s last visit to the currently bookable "
                f"{package_name}{add_on_text}"
                f"{f' ({price_text})' if price_text else ''}."
            )
        return f"{summary}\n\n{content}"

    def display_time(value: object) -> str:
        text = str(value or "")
        return text[:5] if re.fullmatch(r"\d{2}:\d{2}(?::\d{2})?", text) else text

    status = str(result.get("status") or "").lower()
    is_range_success = (
        latest.get("tool") == "check_availability_range"
        and isinstance(result.get("days"), list)
    )
    if status == "missing_information":
        missing = (result.get("data") or {}).get("missing_fields") or []
        field_labels = {
            "room_type": "房型",
            "check_out_date": "退房日期",
            "check_in_time": "check-in/drop-off 时间",
            "duration_minutes": "服务时长",
            "check_out_time_or_duration_minutes": "pickup 时间或服务时长",
        }
        readable = "、".join(field_labels.get(str(field), str(field)) for field in missing)
        if chinese:
            content = f"还需要确认{readable or '完整预约条件'}后才能可靠提供时间；目前不会显示任何未经验证的时段。"
        elif malay:
            content = "Maklumat tempahan belum lengkap, jadi saya tidak akan menawarkan sebarang masa yang belum disahkan. Sila lengkapkan butiran yang diminta dahulu."
        else:
            content = "The booking details are incomplete, so I won’t offer any unverified times. Please provide the missing booking details first."
        return response.model_copy(update={"content": with_repeat_context(content)})
    if status != "success" and not is_range_success:
        if chinese:
            content = "目前无法可靠确认可用时段，因此不会提供任何时间选择。请更换预约条件后让我重新检查，或请员工协助确认。"
        elif malay:
            content = "Saya tidak dapat mengesahkan masa yang tersedia dengan yakin sekarang, jadi tiada pilihan masa akan ditawarkan. Cuba syarat lain atau minta staf menyemak."
        else:
            content = "I can’t reliably verify availability right now, so I won’t offer any time choices. Change a booking condition so I can check again, or ask staff to verify."
        return response.model_copy(update={"content": with_repeat_context(content)})

    if latest.get("tool") == "check_availability_range":
        days = result.get("days") or (result.get("data") or {}).get("days") or []
        verified_days = [
            (
                str(day.get("date") or ""),
                [display_time(slot) for slot in day.get("available_slots") or []],
            )
            for day in days
            if day.get("available_slots")
        ]
        closed_days = [
            (str(day.get("date") or ""), str(day.get("closed_reason")))
            for day in days
            if not day.get("available_slots") and day.get("closed_reason")
        ]
        if verified_days:
            lines = [f"- {date_value}: {', '.join(slots)}" for date_value, slots in verified_days]
            if chinese:
                content = "完整校验后，目前可选择的时段只有：\n" + "\n".join(lines) + "\n请从以上时段中选择。"
            elif malay:
                content = "Selepas semakan penuh, hanya masa berikut tersedia:\n" + "\n".join(lines) + "\nSila pilih daripada masa di atas."
            else:
                content = "After a complete availability check, these are the available times:\n" + "\n".join(lines) + "\nPlease choose only from the times above."
        elif closed_days and len(closed_days) == len(days):
            # Only two real reasons exist for zero choices on every day in
            # range — every day closed means "not operating", never a
            # generic "fully booked" guess.
            reasons = "; ".join(f"{date_value} ({reason})" for date_value, reason in closed_days)
            if chinese:
                content = f"完整校验后，这个日期范围内每一天都不营业：{reasons}。你可以换一个日期范围，我会重新检查。"
            elif malay:
                content = f"Selepas semakan penuh, perniagaan tidak beroperasi pada setiap hari dalam julat ini: {reasons}. Sila berikan julat tarikh lain untuk saya semak semula."
            else:
                content = f"After a complete availability check, the business is not operating on any day in this range: {reasons}. Please give me a different date range and I’ll check again."
        elif chinese:
            content = "完整校验后，这个日期范围目前没有能满足全部条件的可用时段（营业日的员工/房间均已订满）。你可以更换日期或其他预约条件，我会重新检查。"
        elif malay:
            content = "Selepas semakan penuh, tiada masa dalam julat tarikh ini yang memenuhi semua syarat (staf/bilik pada hari beroperasi telah ditempah penuh). Beri tarikh atau syarat lain untuk saya semak semula."
        else:
            content = "After a complete availability check, no times in this date range satisfy all booking conditions — staff/rooms on the operating days are already fully booked. Give me another date or condition and I’ll check again."
        return response.model_copy(update={"content": with_repeat_context(content)})

    data = result.get("data") or {}
    check_out_mode = str(data.get("selection_target") or "").upper() == "CHECK_OUT"
    key = "available_check_out_times" if check_out_mode else "available_slots"
    choices = [display_time(slot) for slot in data.get(key) or []]
    closed_reason = data.get("closed_reason")
    if choices:
        joined = ", ".join(choices)
        if chinese:
            target = "pickup/check-out" if check_out_mode else "check-in/drop-off"
            content = f"完整校验后，目前可选择的 {target} 时间只有：{joined}。请从这些时间中选择。"
        elif malay:
            target = "pickup/check-out" if check_out_mode else "check-in/drop-off"
            content = f"Selepas semakan penuh, hanya masa {target} ini tersedia: {joined}. Sila pilih daripada masa ini."
        else:
            target = "pickup/check-out" if check_out_mode else "check-in/drop-off"
            content = f"After a complete availability check, the only available {target} times are: {joined}. Please choose from these times."
    elif closed_reason:
        # Only two real reasons exist for zero choices — closed_reason present
        # means "not operating", never a generic "fully booked" guess.
        if chinese:
            content = f"完整校验后，这一天不营业（{closed_reason}），因此没有可选时间。你可以换一个日期，我会重新检查。"
        elif malay:
            content = f"Selepas semakan penuh, perniagaan tidak beroperasi pada hari ini ({closed_reason}), jadi tiada masa tersedia. Sila berikan tarikh lain untuk saya semak semula."
        else:
            content = f"After a complete availability check, the business is not operating that day ({closed_reason}), so no times are available. Please give me a different date and I’ll check again."
    elif chinese:
        target = "pickup/check-out" if check_out_mode else "check-in/drop-off"
        content = f"完整校验后，目前没有能满足全部条件的 {target} 时间（当天所有符合条件的员工/房间均已订满）。你可以更换日期、服务时长、房型或员工偏好，我会重新检查。"
    elif malay:
        target = "pickup/check-out" if check_out_mode else "check-in/drop-off"
        content = f"Selepas semakan penuh, tiada masa {target} yang memenuhi semua syarat (semua staf/bilik yang layak telah ditempah). Beri tarikh atau syarat lain untuk saya semak semula."
    else:
        target = "pickup/check-out" if check_out_mode else "check-in/drop-off"
        content = f"After a complete availability check, no {target} time satisfies all booking conditions — every qualified staff member/room for that day is already booked. Give me another date or condition and I’ll check again."
    return response.model_copy(update={"content": with_repeat_context(content)})


def ground_daycare_recommendation_response(
    response: Any, user_message: str, trace: list[dict], state: Any
):
    """Recommend DAYCARE options from structured duration/price evidence."""
    latest = None
    for item in reversed(trace):
        if item.get("tool") in {"resolve_datetime", "update_conversation_state"}:
            continue
        if item.get("tool") != "get_booking_service_options":
            return response
        latest = item
        break
    if latest is None or str((latest.get("args") or {}).get("service_type") or "").upper() != "DAYCARE":
        return response
    try:
        result = json.loads(latest.get("result") or "{}")
    except (TypeError, json.JSONDecodeError):
        return response
    if not isinstance(result, dict) or result.get("status") != "success":
        return response
    options = [
        option for option in (result.get("data") or {}).get("service_options") or []
        if isinstance(option, dict)
    ]
    if not options:
        return response

    recommendation_requested = bool(re.search(
        r"\b(?:recommend|suggest|best|suitable|which\s+(?:one|package)|"
        r"which.{0,30}daycare|what.{0,30}daycare)\b|"
        r"推荐|推薦|建议|建議|适合|適合|哪个好|哪個好",
        user_message or "",
        re.IGNORECASE,
    ))
    booking_daycare = (
        state.active_scenario == "MAKE_BOOKING"
        and str(state.service_type or "").upper() == "DAYCARE"
    )
    if not recommendation_requested and not booking_daycare:
        return response

    duration = getattr(state, "daycare_duration_minutes", None)
    chinese = contains_chinese(user_message)
    if not duration:
        content = (
            "为了推荐正确的日托套餐，请告诉我预计托管多久，或提供接送时间。确认时长后我会比较适用套餐和实际总价。"
            if chinese else
            "To recommend the right daycare package, how long will your pet stay, "
            "or what are the drop-off and pickup times? I’ll compare only the applicable "
            "packages and their actual totals."
        )
        return response.model_copy(update={"content": content})

    ranked: list[tuple[int, float, dict]] = []
    for option in options:
        try:
            rate = float(option.get("price"))
        except (TypeError, ValueError):
            continue
        exact = option.get("duration_minutes")
        minimum = option.get("min_duration_minutes")
        maximum = option.get("max_duration_minutes")
        unit = str(option.get("pricing_unit") or "flat").casefold()
        eligible = False
        rank = 9
        if exact not in (None, ""):
            eligible = int(exact) == int(duration)
            rank = 0
        elif minimum not in (None, "") or maximum not in (None, ""):
            eligible = True
            if minimum not in (None, ""):
                eligible = eligible and (
                    duration > int(minimum)
                    if option.get("min_duration_exclusive")
                    else duration >= int(minimum)
                )
            if maximum not in (None, ""):
                eligible = eligible and (
                    duration < int(maximum)
                    if option.get("max_duration_exclusive")
                    else duration <= int(maximum)
                )
            rank = 1
        elif unit == "hour":
            eligible = True
            rank = 2
        if not eligible:
            continue
        total = rate * duration / 60 if unit == "hour" else rate
        ranked.append((rank, total, option))

    if not ranked:
        return response
    # Price first, not match-specificity first — an exact-duration package
    # is not "the best direct match" if a broader eligible tier/hourly
    # package actually costs less for this duration. rank only breaks a tie
    # between two equally-priced eligible options (preferring the more
    # specific match), and name is the final, fully deterministic tie-break.
    ranked.sort(key=lambda item: (item[1], item[0], str(item[2].get("service_name") or "")))
    recommendations = ranked[:2]

    # Always render the computed comparison — never trust the model's own
    # phrasing here, even if it names an eligible option. Confirmed live: a
    # reply that named the right (cheapest) package and a real ineligible-
    # looking one too can still mislead by listing the more expensive
    # per-unit-rate option first and stating its raw rate ("RM20 per hour")
    # instead of the actual total for the requested duration ("RM140"),
    # making it look cheaper than the flat RM55 option that's actually
    # the better deal. This is real money, not phrasing — unlike
    # ground_direct_datetime_response, a "the name appears somewhere"
    # check is not enough to catch a misleading price presentation.
    duration_text = (
        f"{duration // 60:g} 小时" if duration % 60 == 0 else f"{duration} 分钟"
    ) if chinese else (
        f"{duration // 60:g} hours" if duration % 60 == 0 else f"{duration} minutes"
    )
    rendered = [
        f"{item[2].get('service_name')} — RM{item[1]:g}"
        for item in recommendations
    ]
    if chinese:
        content = f"按 {duration_text} 的托管时长，最适合的是：{rendered[0]}。"
        if len(rendered) > 1:
            content += f" 另一个适用选择是 {rendered[1]}。"
        content += "请选择其中一个；确定后我再按完整时长检查接送时段。"
    else:
        content = f"For a {duration_text} stay, the best direct match is {rendered[0]}."
        if len(rendered) > 1:
            content += f" Another applicable option is {rendered[1]}."
        content += " Choose one, then I’ll check drop-off and pickup availability for the full stay."
    return response.model_copy(update={"content": content})


def ground_document_delivery_response(
    response: Any,
    user_message: str,
    trace: list[dict],
    *,
    trace_has_successful_delivery: Callable[[list[dict]], bool],
):
    """Never let prose turn a failed document dispatch into fake success."""
    document_intent = bool(re.search(
        r"\b(?:confirmation|document|slip|pdf)\b|确认单|确认文件|文件",
        user_message or "",
        re.IGNORECASE,
    ))
    if not document_intent or trace_has_successful_delivery(trace):
        return response
    observed = _latest_result(
        trace,
        {"create_booking", "reschedule_booking", "send_booking_confirmation"},
    )
    if observed is None:
        return response
    _, result = observed
    chinese = contains_chinese(user_message)
    if result.get("handoff_required"):
        truthful = (
            "这次确认单没有成功发送。我已经把文件发送问题记录给员工跟进。"
            if chinese else
            "The confirmation document was not delivered successfully. I've logged the "
            "delivery problem for staff follow-up."
        )
    else:
        truthful = (
            "这次确认单没有发送：系统未能确认可发送的目标预约。请确认预约后再试。"
            if chinese else
            "The confirmation document was not sent because the target booking could not "
            "be verified. Please clarify the booking and try again."
        )
    return response.model_copy(update={"content": truthful})


def ground_membership_response(
    response: Any, user_message: str, trace: list[dict], state: Any = None
):
    """Render membership completion only from an observed successful write."""
    observed = _latest_result(trace, {"register_loyalty_member"})
    if observed is None:
        return response
    _, result = observed
    if result.get("status") != "success":
        return response

    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    tier = data.get("tier") or data.get("membership_status") or "Bronze"
    points = data.get("points_balance")
    points = 0 if points is None else points
    chinese = contains_chinese(user_message)
    if data.get("already_member"):
        truthful = (
            f"您已经是会员。目前等级为 {tier}，积分余额为 {points}。"
            if chinese else
            f"You're already a loyalty member. Your current tier is {tier}, with {points} points."
        )
    else:
        truthful = (
            f"会员注册已完成。您的等级为 {tier}，初始积分为 {points}。"
            if chinese else
            f"Your loyalty membership is now active. Your tier is {tier}, with {points} points."
        )
    if state is not None and state.active_scenario == "MAKE_BOOKING":
        existing = str(response.content or "").strip()
        combined = "\n\n".join(part for part in (truthful, existing) if part)
        return response.model_copy(update={"content": combined})
    return response.model_copy(update={"content": truthful})


def ground_booking_preview_response(response: Any, user_message: str, trace: list[dict]):
    """Render the exact observed booking preview instead of model prose."""
    observed = _latest_result(trace, {"create_booking"})
    if observed is None:
        return response
    _, result = observed
    if result.get("status") != "confirmation_required":
        return response
    preview = (result.get("data") or {}).get("preview")
    if not isinstance(preview, dict):
        return response

    pet = preview.get("pet_name") or "your pet"
    package = preview.get("package_name") or preview.get("service_type") or "service"
    date_value = preview.get("date") or ""
    start = str(preview.get("time") or "")[:5]
    checkout = str(preview.get("check_out_time") or "")[:5]
    duration = preview.get("duration_minutes")
    price = preview.get("price")
    price_text = f"RM{float(price):g}" if price not in (None, "") else ""
    chinese = contains_chinese(user_message)
    if chinese:
        timing = f"{start} 至 {checkout}" if checkout else (
            f"{start}，时长 {duration} 分钟" if duration else start
        )
        content = (
            f"请确认预约：{pet}，{package}，{date_value} {timing}"
            f"{f'，{price_text}' if price_text else ''}。回复“确认”后才会正式建立预约。"
        )
    else:
        timing = f"{start} to {checkout}" if checkout else (
            f"{start} for {duration} minutes" if duration else start
        )
        content = (
            f"Please confirm this booking for {pet}: {package} on {date_value}, "
            f"{timing}{f', {price_text}' if price_text else ''}. "
            "Reply yes to create the booking."
        )
    return response.model_copy(update={"content": content})


def ground_unavailable_profile_claims(response: Any, user_message: str, customer: dict):
    """Never let a failed profile read become a verified-empty claim."""
    content = str(response.content or "")
    false_no_pet_claim = bool(re.search(
        r"\b(?:no|don't|do not|doesn't|does not|haven't|have not)\b.{0,35}"
        r"\bpets?\b|没有.{0,8}宠物|未.{0,8}(?:登记|注册).{0,8}宠物",
        content,
        re.IGNORECASE,
    ))
    false_no_booking_claim = bool(re.search(
        r"\b(?:no|don't|do not|doesn't|does not|haven't|have not)\b.{0,40}"
        r"\bbookings?\b|没有.{0,8}预约|查不到.{0,8}预约",
        content,
        re.IGNORECASE,
    ))
    pets_failed = customer.get("pets_context_status") == "unavailable"
    booking_failed = customer.get("booking_context_status") == "unavailable"
    if not ((pets_failed and false_no_pet_claim) or (booking_failed and false_no_booking_claim)):
        return response

    chinese = contains_chinese(user_message)
    truthful = (
        "目前无法读取您的客户、宠物或预约资料，因此我不能判断记录为空。请稍后再试。"
        if chinese else
        "I couldn't read your customer, pet, or booking profile just now, so I can't "
        "truthfully say that no record exists. Please try again shortly."
    )
    return response.model_copy(update={"content": truthful})

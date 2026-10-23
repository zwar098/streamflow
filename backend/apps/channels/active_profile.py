"""Effective automation profile summary shared by channel list and detail APIs."""


def automation_profile_summary(automation_config, channel_id, group_id):
    result = {"profile_id": None, "profile_name": None, "period_name": None, "source": None}
    config = automation_config.get_effective_configuration(channel_id, group_id)
    if config:
        profile = config.get("profile") or {}
        result["profile_id"] = profile.get("id")
        result["profile_name"] = profile.get("name")
        result["period_name"] = config.get("period_name")
        channel_periods = automation_config.get_channel_periods(channel_id)
        period_id = config.get("period_id")
        result["source"] = "channel" if period_id and str(period_id) in channel_periods else "group"
    return result


def enrich_active_profiles(channels, automation_config):
    """Resolve the full list with a fixed number of SQL reads, never per-channel queries."""
    if not channels:
        return channels
    previous = automation_config.begin_run_snapshot()
    try:
        for channel in channels:
            group_id = channel.get("group_id")
            if group_id is None:
                group_id = channel.get("channel_group_id")
            automation = automation_profile_summary(automation_config, channel.get("id"), group_id)
            epg_id = channel.get("epg_scheduled_profile_id")
            epg_profile = automation_config.get_profile(epg_id) if epg_id else None
            channel["active_profile"] = {
                "automation": automation,
                "epg_override": {
                    "profile_name": epg_profile.get("name") if epg_profile else None,
                    "source": ("channel" if channel.get("channel_epg_scheduled_profile_id") else "group") if epg_profile else None,
                },
            }
    finally:
        automation_config.end_run_snapshot(previous)
    return channels

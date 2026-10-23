# Channel list sorting and profile filter

Open **Channels -> Channels**. Click **Channel #**, **Name**, or **Streams** in
the desktop list header to sort ascending; click the same button again to sort
descending. The **Sort channels** dropdown offers the same options on every
screen size, plus **Saved channel order**. Streams means the assigned stream
count shown in each row.

Use **Effective automation profile** to show **All profiles**, one selected
profile, or **No profile configured**. The filter follows the effective profile
shown in the row: it resolves enabled automation periods, channel overrides,
group inheritance and period priority using the stream checker's resolver.
It does not filter on EPG overrides or legacy direct profile assignments.

Sorting and filtering apply to the whole channel list before pagination and
combine with search, the group filter and **Channel tools -> Sort by Group**.
With group sorting enabled, the selected sort applies within each group.
Changing a filter or sort returns to page one. **Reset view** restores saved
order and clears the profile filter and group sorting. Search and the group
filter remain as selected.

These controls change the current view only. To change the stored processing
order, use the separate **Channel Order** tab and save its changes.

The channel list requests `GET /api/channels?include_active_profile=true` to
load every row's profile summary before filtering. Other API callers retain
the original payload unless they opt in. Profile resolution uses a request-local
configuration snapshot and a fixed number of database reads rather than one
request per channel. Refreshing group configuration also refreshes these summaries.
Match-count scans prepare and validate patterns once per channel. Rapid view
changes keep one scan running and skip superseded queued views so sorting does
not flood the API with concurrent full-inventory scans.

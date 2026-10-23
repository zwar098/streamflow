const collator = new Intl.Collator(undefined, { numeric: true, sensitivity: 'base' })

export const CHANNEL_SORT_OPTIONS = [
  { key: 'channel_number', label: 'Channel number' },
  { key: 'name', label: 'Channel name' },
  { key: 'streams', label: 'Assigned streams' },
]

// View changes can supersede a slow preview, but its server-side work continues.
// Run one scan at a time and skip queued views that are no longer displayed.
export function createMatchCountRunner() {
  let pending = Promise.resolve()
  return (request, isCancelled) => {
    pending = pending.catch(() => {}).then(() => isCancelled() ? null : request())
    return pending
  }
}

export function matchesProfileFilter(activeProfile, profileFilter) {
  if (profileFilter === 'all') return true
  // An unresolved or failed lookup must never be presented as "No profile".
  if (!activeProfile?.automation || activeProfile.error) return false
  const id = activeProfile.automation.profile_id
  return profileFilter === 'none' ? id == null : String(id) === profileFilter
}

function channelNumber(channel) {
  const value = channel.channel_number
  if (value == null || String(value).trim() === '') return null
  const number = Number(value)
  return Number.isFinite(number) ? number : null
}

export function sortChannelList(channels, sort, sortByGroup = false, groupsById = new Map()) {
  return [...channels].sort((a, b) => {
    if (sortByGroup) {
      const group = collator.compare(groupsById.get(String(a.channel_group_id))?.name || '', groupsById.get(String(b.channel_group_id))?.name || '')
      if (group) return group
    }
    const key = sort.key === 'custom' && sortByGroup ? 'channel_number' : sort.key
    const direction = sort.direction === 'desc' ? -1 : 1
    if (key === 'name') return direction * collator.compare(a.name || '', b.name || '')
    if (key === 'streams') return direction * ((a.streams?.length ?? 0) - (b.streams?.length ?? 0))
    if (key === 'channel_number') {
      const first = channelNumber(a)
      const second = channelNumber(b)
      // Missing channel numbers stay last in both directions; zero is a valid number.
      if (first == null || second == null) return first == null ? (second == null ? 0 : 1) : -1
      return direction * (first - second)
    }
    return 0
  })
}

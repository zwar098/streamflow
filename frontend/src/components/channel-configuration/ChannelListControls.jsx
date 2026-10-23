import { ArrowDown, ArrowUp, ArrowUpDown } from 'lucide-react'
import { Button } from '@/components/ui/button.jsx'
import { Label } from '@/components/ui/label.jsx'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select.jsx'
import { CHANNEL_SORT_OPTIONS } from './channelListView.js'

export function ChannelSortButton({ field, label, sort, onSort }) {
  const selected = sort.key === field
  const Icon = selected ? (sort.direction === 'asc' ? ArrowUp : ArrowDown) : ArrowUpDown
  return (
    <Button type="button" variant="ghost" size="sm" className="min-h-11 gap-1 px-2 text-xs" onClick={() => onSort(field)}
      aria-label={`Sort by ${label.toLowerCase()} ${selected && sort.direction === 'asc' ? 'descending' : 'ascending'}`}>
      {label}<Icon aria-hidden="true" className="h-3.5 w-3.5 shrink-0" />
      {selected && <span className="sr-only">Currently {sort.direction === 'asc' ? 'ascending' : 'descending'}</span>}
    </Button>
  )
}

export function ChannelListControls({ sort, onSortChange, profileFilter, onProfileFilterChange, profiles, onReset, canReset }) {
  return (
    <div className="grid grid-cols-1 items-end gap-2 sm:flex sm:flex-wrap">
      <div className="w-full min-w-0 sm:w-60">
        <Label htmlFor="channel-list-sort" className="mb-1 block text-xs">Sort channels</Label>
        <Select value={sort.key === 'custom' ? 'custom' : `${sort.key}:${sort.direction}`}
          onValueChange={value => {
            const [key, direction = 'asc'] = value.split(':')
            onSortChange({ key, direction })
          }}>
          <SelectTrigger id="channel-list-sort" className="h-11 w-full"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="custom">Saved channel order</SelectItem>
            {CHANNEL_SORT_OPTIONS.flatMap(({ key, label }) => ['asc', 'desc'].map(direction => (
              <SelectItem key={`${key}:${direction}`} value={`${key}:${direction}`}>{label} ({direction === 'asc' ? 'ascending' : 'descending'})</SelectItem>
            )))}
          </SelectContent>
        </Select>
      </div>
      <div className="w-full min-w-0 sm:w-60">
        <Label htmlFor="channel-profile-filter" className="mb-1 block text-xs">Effective automation profile</Label>
        <Select value={profileFilter} onValueChange={onProfileFilterChange}>
          <SelectTrigger id="channel-profile-filter" className="h-11 w-full"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All profiles</SelectItem>
            <SelectItem value="none">No profile configured</SelectItem>
            {[...profiles].sort((a, b) => a.name.localeCompare(b.name)).map(profile => (
              <SelectItem key={profile.id} value={String(profile.id)}>{profile.name}</SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      {canReset && <Button variant="outline" className="min-h-11 justify-self-start" onClick={onReset}>Reset view</Button>}
    </div>
  )
}

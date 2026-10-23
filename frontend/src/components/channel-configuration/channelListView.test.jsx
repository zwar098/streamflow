import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { createMatchCountRunner, matchesProfileFilter, sortChannelList } from './channelListView.js'
import { ChannelSortButton } from './ChannelListControls.jsx'

const ids = channels => channels.map(channel => channel.id)

describe('match-count scans during rapid view changes', () => {
  it('keeps one scan in flight and skips superseded queued views', async () => {
    const run = createMatchCountRunner()
    const calls = []
    let finish
    let markStarted
    const started = new Promise(resolve => { markStarted = resolve })
    const first = run(() => {
      calls.push('first')
      markStarted()
      return new Promise(resolve => { finish = resolve })
    }, () => false)
    await started
    const skipped = run(() => { calls.push('skipped') }, () => true)
    const latest = run(() => { calls.push('latest'); return 2 }, () => false)
    expect(calls).toEqual(['first'])
    finish(1)
    expect(await first).toBe(1)
    expect(await skipped).toBeNull()
    expect(await latest).toBe(2)
    expect(calls).toEqual(['first', 'latest'])
  })

  it('allows the current view to load after an earlier request fails', async () => {
    const run = createMatchCountRunner()
    await expect(run(() => Promise.reject(new Error('previous request failed')), () => false)).rejects.toThrow('previous request failed')
    expect(await run(() => 5, () => false)).toBe(5)
  })
})

describe('channel list sorting', () => {
  it('sorts numeric channel numbers including zero and decimals; missing values remain last', () => {
    const channels = [
      { id: 1, channel_number: '10' }, { id: 2, channel_number: '2.5' },
      { id: 3, channel_number: 0 }, { id: 4, channel_number: null },
      { id: 5, channel_number: '' }, { id: 6, channel_number: 'N/A' },
    ]
    expect(ids(sortChannelList(channels, { key: 'channel_number', direction: 'asc' }))).toEqual([3, 2, 1, 4, 5, 6])
    expect(ids(sortChannelList(channels, { key: 'channel_number', direction: 'desc' }))).toEqual([1, 2, 3, 4, 5, 6])
    expect(ids(channels)).toEqual([1, 2, 3, 4, 5, 6])
  })

  it('sorts names naturally in both directions and preserves equal-name order', () => {
    const channels = [{ id: 1, name: 'Channel 10' }, { id: 2, name: 'channel 2' }, { id: 3, name: 'CHANNEL 2' }]
    expect(ids(sortChannelList(channels, { key: 'name', direction: 'asc' }))).toEqual([2, 3, 1])
    expect(ids(sortChannelList(channels, { key: 'name', direction: 'desc' }))).toEqual([1, 2, 3])
  })

  it('sorts the full set by assigned streams before pagination', () => {
    const channels = Array.from({ length: 45 }, (_, id) => ({ id, streams: Array(id % 9).fill(id), matchCount: 100 - id }))
    const sorted = sortChannelList(channels, { key: 'streams', direction: 'desc' })
    expect(sorted.slice(0, 5).map(channel => channel.streams.length)).toEqual([8, 8, 8, 8, 8])
    expect(sorted.slice(20, 25).map(channel => channel.streams.length)).toEqual([4, 4, 4, 4, 4])
    expect(sortChannelList([{ id: 1 }, { id: 2, streams: [1] }], { key: 'streams', direction: 'asc' })[0].id).toBe(1)
  })

  it('restores saved order and supports sorting within groups without mutating it', () => {
    const channels = [{ id: 1, name: 'Zulu', channel_group_id: 2 }, { id: 2, name: 'Alpha', channel_group_id: 1 }, { id: 3, name: 'Beta', channel_group_id: 2 }]
    const groups = new Map([['1', { name: 'B' }], ['2', { name: 'A' }]])
    expect(ids(sortChannelList(channels, { key: 'name', direction: 'asc' }, true, groups))).toEqual([3, 1, 2])
    expect(ids(sortChannelList(channels, { key: 'custom', direction: 'asc' }))).toEqual([1, 2, 3])
  })
})

describe('effective automation profile filtering', () => {
  it('uses profile IDs and includes group-inherited effective profiles', () => {
    const profile = { automation: { profile_id: '2', profile_name: 'Shared name', source: 'group' } }
    expect(matchesProfileFilter(profile, '2')).toBe(true)
    expect(matchesProfileFilter(profile, '3')).toBe(false)
    expect(matchesProfileFilter(profile, 'none')).toBe(false)
    expect(matchesProfileFilter({ automation: { profile_id: 2 } }, '2')).toBe(true)
  })

  it('distinguishes no automation profile from an EPG override, pending data and lookup failures', () => {
    expect(matchesProfileFilter({ automation: { profile_id: null }, epg_override: { profile_name: 'EPG' } }, 'none')).toBe(true)
    for (const value of [null, undefined, { error: true }, {}]) {
      expect(matchesProfileFilter(value, 'none')).toBe(false)
      expect(matchesProfileFilter(value, '2')).toBe(false)
      expect(matchesProfileFilter(value, 'all')).toBe(true)
    }
  })
})

describe('sortable column buttons', () => {
  it('announces the current direction and switches to the opposite direction on the next click', () => {
    const html = renderToStaticMarkup(<ChannelSortButton field="streams" label="Streams" sort={{ key: 'streams', direction: 'asc' }} />)
    expect(html).toContain('Sort by streams descending')
    expect(html).toContain('Currently ascending')
    expect(html).toContain('type="button"')
  })
})

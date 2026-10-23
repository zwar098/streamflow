import { describe, expect, it } from 'vitest'

import {
  filterTeamarrEventsBySearch,
  filterTeamarrEventsByView,
  paginateTeamarrEvents,
  sortTeamarrManagedEvents,
} from './teamarr-preflight-event-search'

describe('Teamarr preflight event search helpers', () => {
  const events = [
    {
      event_name: 'Netherlands vs. Algeria',
      channel_name: 'DE Soccer PPV 101',
      sport: 'soccer',
      league: 'fifa.friendly',
      state: 'scheduled',
      last_preflight_event: { type: 'preflight_completed' },
    },
    {
      event_name: 'Detroit Tigers at Boston Red Sox',
      channel_name: 'MLB Event 5',
      sport: 'baseball',
      league: 'mlb',
      state: 'past',
      bucket: 'manual',
      details: { reason: 'active_viewers' },
    },
    {
      preflight_kind: 'team',
      event_name: 'Static Team Row',
      team_name: 'San Jose Sharks',
      team_abbrev: 'SJS',
      channel_name: 'Team Channel',
      sport: 'hockey',
      league: 'nhl',
      team_status: 'ready',
      state: 'due',
    },
  ]

  it('returns all events when search is empty', () => {
    expect(filterTeamarrEventsBySearch(events, '')).toBe(events)
  })

  it('matches event names, channels, sport, league, and state', () => {
    expect(filterTeamarrEventsBySearch(events, 'algeria')).toEqual([events[0]])
    expect(filterTeamarrEventsBySearch(events, 'soccer ppv')).toEqual([events[0]])
    expect(filterTeamarrEventsBySearch(events, 'baseball')).toEqual([events[1]])
    expect(filterTeamarrEventsBySearch(events, 'past')).toEqual([events[1]])
    expect(filterTeamarrEventsBySearch(events, 'manual')).toEqual([events[1]])
    expect(filterTeamarrEventsBySearch(events, 'sharks')).toEqual([events[2]])
    expect(filterTeamarrEventsBySearch(events, 'sjs')).toEqual([events[2]])
    expect(filterTeamarrEventsBySearch(events, 'hockey')).toEqual([events[2]])
  })

  it('matches latest preflight and decision detail fields', () => {
    expect(filterTeamarrEventsBySearch(events, 'completed')).toEqual([events[0]])
    expect(filterTeamarrEventsBySearch(events, 'active_viewers')).toEqual([events[1]])
  })

  it('sorts current and upcoming managed events before past events', () => {
    const sorted = sortTeamarrManagedEvents([
      { event_name: 'Oldest past', state: 'past', seconds_to_start: -7200 },
      { event_name: 'Upcoming later', state: 'scheduled', seconds_to_start: 3600 },
      { event_name: 'Due now', state: 'due', seconds_to_start: -30 },
      { event_name: 'Recent past', state: 'past', seconds_to_start: -300 },
    ])

    expect(sorted.map(event => event.event_name)).toEqual([
      'Due now',
      'Upcoming later',
      'Recent past',
      'Oldest past',
    ])
  })

  it('filters managed events by operator view', () => {
    expect(filterTeamarrEventsByView(events, 'upcoming')).toEqual([events[0], events[2]])
    expect(filterTeamarrEventsByView(events, 'past')).toEqual([events[1]])
    expect(filterTeamarrEventsByView(events, 'no_check')).toEqual([events[1], events[2]])
    expect(filterTeamarrEventsByView(events, 'all')).toEqual(events)
  })

  it('hides filtered events and teams from normal views while retaining explicit diagnostic views', () => {
    const excluded = [
      { preflight_kind: 'event', event_name: 'Excluded sport', sport: 'baseball', state: 'filtered' },
      { preflight_kind: 'event', event_name: 'Excluded league', league: 'nba', state: 'filtered', last_preflight_event: { type: 'preflight_completed' } },
      { preflight_kind: 'team', team_name: 'Outside include scope', state: 'filtered' },
      { event_name: 'Legacy event without kind', state: 'filtered' },
    ]
    const candidates = [...events, ...excluded]

    for (const view of ['upcoming', 'no_check', 'due', 'past']) {
      expect(filterTeamarrEventsByView(candidates, view)).toEqual(filterTeamarrEventsByView(events, view))
    }
    expect(filterTeamarrEventsByView(candidates, 'filtered')).toEqual(excluded)
    expect(filterTeamarrEventsByView(candidates, 'all')).toEqual(candidates)
  })

  it('searches and paginates the selected view without leaking filtered items into normal lists', () => {
    const candidates = Array.from({ length: 24 }, (_, index) => ({
      preflight_kind: index % 2 ? 'team' : 'event',
      event_name: `League entry ${index}`,
      state: index < 12 ? 'filtered' : 'due',
    }))
    const visible = filterTeamarrEventsBySearch(filterTeamarrEventsByView(candidates, 'upcoming'), 'league')
    const filtered = filterTeamarrEventsBySearch(filterTeamarrEventsByView(candidates, 'filtered'), 'league')

    expect(visible).toHaveLength(12)
    expect(filtered).toHaveLength(12)
    expect(paginateTeamarrEvents(visible, 2, 10)).toEqual(candidates.slice(22))
    expect(paginateTeamarrEvents(filtered, 2, 10)).toEqual(candidates.slice(10, 12))
    expect(filterTeamarrEventsByView([], 'filtered')).toEqual([])
    expect(filterTeamarrEventsByView(events, 'filtered')).toEqual([])
  })

  it('keeps static team diagnostics out of the default upcoming view', () => {
    const config = { preflight_offset_minutes: 20, poll_interval_seconds: 30 }
    const staticTeams = [
      {
        preflight_kind: 'team',
        event_name: 'Chicago Cubs',
        state: 'no_dispatcharr_channel',
        seconds_to_start: -18600,
      },
      {
        preflight_kind: 'team',
        event_name: 'New York Yankees',
        state: 'no_live_window',
      },
      {
        preflight_kind: 'team',
        event_name: 'Arizona Cardinals',
        state: 'no_event_window',
        event_date: '2026-06-13T16:00:00Z',
        seconds_to_start: -170,
        dispatcharr_channel_id: 645,
        stream_count: 1,
        team_status: 'ready',
      },
      {
        preflight_kind: 'team',
        event_name: 'Arizona Cardinals',
        state: 'scheduled',
        event_date: '2026-06-13T22:05:00Z',
        seconds_to_start: 7200,
        dispatcharr_channel_id: 458,
        stream_count: 1,
        team_status: 'ready',
      },
      {
        preflight_kind: 'team',
        event_name: 'San Francisco Giants',
        state: 'scheduled',
        event_date: '2026-06-13T22:05:00Z',
        seconds_to_start: 1200,
        dispatcharr_channel_id: 458,
        stream_count: 1,
        team_status: 'ready',
      },
      {
        preflight_kind: 'team',
        event_name: 'Static Team Due',
        state: 'due',
        dispatcharr_channel_id: 459,
      },
    ]

    expect(filterTeamarrEventsByView(staticTeams, 'upcoming', config)).toEqual([
      staticTeams[4],
      staticTeams[5],
    ])
    expect(filterTeamarrEventsByView(staticTeams, 'all', config)).toEqual(staticTeams)
  })

  it('keeps non-checkable static team diagnostics out of the no-check view', () => {
    const config = { preflight_offset_minutes: 20, poll_interval_seconds: 30 }
    const staticTeams = [
      {
        preflight_kind: 'team',
        event_name: 'Miami Marlins',
        state: 'no_dispatcharr_channel',
        event_date: '2026-06-14T16:15:00Z',
        seconds_to_start: -3600,
      },
      {
        preflight_kind: 'team',
        event_name: 'Arizona Cardinals',
        state: 'no_live_window',
      },
      {
        preflight_kind: 'team',
        event_name: 'Static Team Due',
        state: 'due',
        dispatcharr_channel_id: 459,
      },
      {
        preflight_kind: 'event',
        event_name: 'Managed Event Without Check',
        state: 'past',
      },
    ]

    expect(filterTeamarrEventsByView(staticTeams, 'no_check', config)).toEqual([
      staticTeams[2],
      staticTeams[3],
    ])
    expect(filterTeamarrEventsByView(staticTeams, 'all', config)).toEqual(staticTeams)
  })

  it('does not hide scheduled managed events outside the static team window', () => {
    const managedEvent = {
      preflight_kind: 'event',
      event_name: 'Managed MLB Event',
      state: 'scheduled',
      seconds_to_start: 7200,
    }
    const staticTeam = {
      preflight_kind: 'team',
      event_name: 'Static MLB Team',
      state: 'scheduled',
      event_date: '2026-06-13T22:05:00Z',
      seconds_to_start: 7200,
      dispatcharr_channel_id: 458,
      stream_count: 1,
      team_status: 'ready',
    }

    expect(filterTeamarrEventsByView([managedEvent, staticTeam], 'upcoming', {
      preflight_offset_minutes: 20,
      poll_interval_seconds: 30,
    })).toEqual([managedEvent])
  })

  it('paginates managed event lists', () => {
    expect(paginateTeamarrEvents([1, 2, 3, 4, 5], 2, 2)).toEqual([3, 4])
    expect(paginateTeamarrEvents([1, 2, 3], 0, 2)).toEqual([1, 2])
  })
})

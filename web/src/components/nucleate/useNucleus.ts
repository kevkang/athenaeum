import { useEffect, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { nucleateApi, type Expansion, type ExpansionEvent, type NucleusDetail } from '../../api/nucleate';

/**
 * One nucleus's canvas data, plus the live events of its latest expansion.
 *
 * While a step is running, the nucleus is re-read every two seconds so nodes
 * appear as the worker commits each one (progressive rendering), and the run's
 * events are polled every second, asking only for those after the last one
 * seen — that is what gives the stage list real, current counts. Both stop as
 * soon as the run ends. Polling rather than the websocket matches the Audit
 * tab; the backend's /expansions/{id}/stream is there if this ever needs to
 * be cheaper.
 */
export function useNucleus(nucleusId: string | null) {
  const detail = useQuery({
    queryKey: ['nucleus', nucleusId],
    queryFn: () => nucleateApi.get(nucleusId!),
    enabled: !!nucleusId,
    refetchInterval: (q) => (latestExpansion(q.state.data)?.status === 'running' ? 2000 : false),
  });

  const latest = latestExpansion(detail.data);
  const [events, setEvents] = useState<ExpansionEvent[]>([]);
  const [run, setRun] = useState<Expansion | null>(null);
  const lastOrd = useRef(-1);
  const trackedId = useRef<string | null>(null);

  useEffect(() => {
    const expansionId = latest?.id ?? null;
    if (expansionId !== trackedId.current) {
      // A different run: start its event log afresh.
      trackedId.current = expansionId;
      lastOrd.current = -1;
      setEvents([]);
      setRun(latest ?? null);
    }
    if (!expansionId) return;

    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const poll = async () => {
      try {
        const res = await nucleateApi.expansion(expansionId, lastOrd.current);
        if (cancelled) return;
        if (res.events.length) {
          lastOrd.current = res.events[res.events.length - 1].ord;
          setEvents((prev) => [...prev, ...res.events]);
        }
        setRun(res.expansion);
        if (res.expansion.status === 'running') timer = setTimeout(poll, 1000);
      } catch {
        if (!cancelled) timer = setTimeout(poll, 3000);
      }
    };
    void poll();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
    // Re-subscribe only when the run itself changes, or it starts/stops running.
  }, [latest?.id, latest?.status]); // eslint-disable-line react-hooks/exhaustive-deps

  return { detail: detail.data, isLoading: detail.isLoading, refetch: detail.refetch, run, events };
}

export function latestExpansion(detail: NucleusDetail | undefined): Expansion | null {
  if (!detail) return null;
  for (let i = detail.prompts.length - 1; i >= 0; i--) {
    const exp = detail.prompts[i].expansion;
    if (exp) return exp;
  }
  return null;
}

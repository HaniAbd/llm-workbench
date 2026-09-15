"use client";

import {
  createContext,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import { API_BASE } from "../lib/api";

/** The similarity floor, read from the API that enforces it.
 *
 *  It used to be written twice: `SIMILARITY_FLOOR = 0.52` in
 *  `api/answering.py`, which decides whether a question is answered at all,
 *  and `WEAK_MATCH_BELOW = 0.55` here, which decided what the interface called
 *  weak. Nothing reconciled them, so the interface called a match weak that
 *  the API had accepted - a measured example is "What does a finish reason of
 *  length mean?", answered on a top score of 0.528.
 *
 *  This is not a response schema, so it does not belong in the generated
 *  types. It is a property of the running server, and `GET /retrieval/config`
 *  already publishes it. Read at runtime rather than baked at build time,
 *  because a value compiled in is the same bug again, just slower to notice.
 *
 *  `null` means genuinely unknown - not yet loaded, unreachable, or published
 *  under a key this build does not recognise. Nothing falls back to a
 *  hardcoded number: a wrong threshold presented confidently is the thing
 *  being fixed. The interface says it does not know instead. */
const FLOOR_KEY = "answering.SIMILARITY_FLOOR";

type State = { floor: number | null; loaded: boolean };

const RetrievalConfigContext = createContext<State>({ floor: null, loaded: false });

export function RetrievalConfigProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<State>({ floor: null, loaded: false });

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch(`${API_BASE}/retrieval/config`);
        const body = await res.json();
        const value = body?.config?.[FLOOR_KEY];
        if (!cancelled) {
          setState({
            floor: typeof value === "number" ? value : null,
            loaded: true,
          });
        }
      } catch {
        if (!cancelled) setState({ floor: null, loaded: true });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <RetrievalConfigContext.Provider value={state}>
      {children}
    </RetrievalConfigContext.Provider>
  );
}

export function useSimilarityFloor() {
  return useContext(RetrievalConfigContext);
}

/** Below the floor the API refuses outright, so a passage under it is one that
 *  would not have carried an answer on its own. With no floor known, no
 *  verdict is offered. */
export function isBelowFloor(score: number, floor: number | null): boolean {
  return floor !== null && score < floor;
}

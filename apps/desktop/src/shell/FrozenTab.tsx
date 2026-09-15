import { cloneElement, isValidElement, useRef, type ReactElement } from "react";

type ActiveProps = { active?: boolean };

/**
 * Freeze inactive tab trees: keep the last element reference so App polls / sibling
 * tab flips don't reconcile huge pages. Still forward `active` so page effects can sleep.
 */
export function FrozenTab({ frozen, children }: { frozen: boolean; children: ReactElement }) {
  const cacheRef = useRef(children);
  if (!frozen) {
    cacheRef.current = children;
  } else if (isValidElement(children) && isValidElement(cacheRef.current)) {
    const next = children.props as ActiveProps;
    const prev = cacheRef.current.props as ActiveProps;
    if ("active" in next && next.active !== prev.active) {
      cacheRef.current = cloneElement(cacheRef.current, { active: next.active } as Partial<ActiveProps>);
    }
  }
  return cacheRef.current;
}

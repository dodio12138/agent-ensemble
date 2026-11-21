import { useEffect, useRef, useState, type CSSProperties } from 'react'

export function Range({
  label, value, disabled, onCommit,
}: {
  label: string
  value: number
  disabled: boolean
  onCommit: (value: number) => void
}) {
  const [draft, setDraft] = useState(Math.round(value * 100))
  const lastSubmitted = useRef(draft)
  useEffect(() => {
    const next = Math.round(value * 100)
    setDraft(next)
    lastSubmitted.current = next
  }, [value])
  function save() {
    if (draft !== lastSubmitted.current) {
      lastSubmitted.current = draft
      onCommit(draft / 100)
    }
  }
  return (
    <div className="range">
      <input
        aria-label={label}
        type="range"
        min={0}
        max={100}
        value={draft}
        disabled={disabled}
        style={{ '--level': `${draft}%` } as CSSProperties}
        onChange={event => setDraft(Number(event.target.value))}
        onPointerUp={save}
        onKeyUp={save}
        onBlur={save}
      />
      <output>{draft}%</output>
    </div>
  )
}

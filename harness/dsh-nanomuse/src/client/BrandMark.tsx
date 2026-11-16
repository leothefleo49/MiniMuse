/**
 * The MiniMuse app's own mark: the stylized MM strokes on a white squircle tile with a
 * hairline edge, matching `assets/brand/nanomuse-icon.svg`.
 */
import { createElement as h, useId, type ReactNode } from 'react'

/** The MM stroke path, in the tile's 100 x 100 coordinates. */
const STROKE = 'M14.5 70 L14.5 30 L28.5 57 L42.5 30 L42.5 70 M57.5 70 L57.5 30 L71.5 57 L85.5 30 L85.5 70'

export function BrandMark({ size = 72, className }: { size?: number; className?: string }): ReactNode {
  // one gradient per instance: two marks on one page must not share an id
  const id = `mm-mark-${useId().replace(/[^a-zA-Z0-9_-]/g, '')}`
  return h('svg', {
    width: size,
    height: size,
    viewBox: '0 0 100 100',
    className: `nm-brand-mark${className ? ` ${className}` : ''}`,
    role: 'img',
    'aria-label': 'MiniMuse',
    focusable: false,
  },
    h('defs', null,
      h('linearGradient', { id, gradientUnits: 'userSpaceOnUse', x1: 12, y1: 50, x2: 88, y2: 50 },
        h('stop', { offset: 0, stopColor: '#015CFB' }),
        h('stop', { offset: 1, stopColor: '#0186FB' }))),
    // the tile with its hairline edge, so it keeps its shape on a white page
    h('rect', { x: 0.3, y: 0.3, width: 99.4, height: 99.4, rx: 27, ry: 27, fill: '#FFFFFF', stroke: '#D9D9DE', strokeWidth: 0.6 }),
    h('path', {
      d: STROKE,
      fill: 'none',
      stroke: `url(#${id})`,
      strokeWidth: 8.5,
      strokeLinecap: 'round',
      strokeLinejoin: 'round',
    }))
}

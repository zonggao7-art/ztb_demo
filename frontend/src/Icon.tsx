import type { CSSProperties } from 'react'

const paths = {
  company: 'M4 21V5h11v16 M15 10h5v11 M2 21h20 M8 9h3 M8 13h3 M8 17h3',
  logo: 'M5 3h10l4 4v14H5z M14 3v5h5 M9 12h6 M9 16h4',
  plus: 'M12 5v14 M5 12h14',
  chat: 'M21 11.5a8.5 8.5 0 0 1-8.5 8.5H4l-2 2v-9.5A8.5 8.5 0 0 1 10.5 4h2A8.5 8.5 0 0 1 21 11.5Z M7 10h9 M7 14h6',
  book: 'M12 5v16 M3 4c3-1 6-1 9 1 3-2 6-2 9-1v15c-3-1-6-1-9 1-3-2-6-2-9-1Z',
  chart: 'M4 3v17h17 M8 15v-4 M13 15V7 M18 15v-7',
  spark: 'm12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5Z',
  arrow: 'M5 12h14 M13 6l6 6-6 6',
  up: 'M12 19V5 M5 12l7-7 7 7',
  chevron: 'm9 5 7 7-7 7',
  check: 'm5 12 4 4L19 6',
  shield: 'm12 3 8 3v6c0 4-4 7-8 9-4-2-8-5-8-9V6Z m-4 9 3 3 5-6',
  copy: 'M9 9h11v12H9z M15 9V3H3v12h6',
  retry: 'M3 10a9 9 0 1 1 1 7 M3 4v6h6',
  clock: 'M12 7v5l3 2 M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0',
  info: 'M12 11v6 M12 7h.01 M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0',
  menu: 'M4 6h16 M4 12h16 M4 18h16',
  close: 'm6 6 12 12 M6 18 18 6',
  stop: 'M6 6h12v12H6Z',
} as const
export type IconName = keyof typeof paths
export default function Icon({ name, size = 20, style, className }: { name: IconName; size?: number; style?: CSSProperties; className?: string }) {
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.65" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" style={style} className={className}><path d={paths[name]} /></svg>
}

export default function Brand({ size = 40 }: { size?: number }) {
  return <img src="/favicon.svg" width={size} height={size} alt="MusicPlayer" className="shrink-0" />
}

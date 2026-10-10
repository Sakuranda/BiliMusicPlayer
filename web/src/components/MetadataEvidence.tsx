import type { MetadataSuggestion } from '../types'

const sourceNames: Record<string, string> = {
  manual: '手动确认', brackets: '标题书名号', title_brackets: '标题书名号',
  title: '视频标题', tags: '视频标签', tag: '视频标签', description: '视频简介',
  known_artist: '曲库歌手', known_artists: '曲库歌手', uploader: '上传者',
  fallback: '信息不足', unknown: '信息不足', raw_title: '原标题待核对',
  title_book: '标题书名号', title_books: '多首作品标题', title_bracket: '标题人名括号',
  title_credit: '标题署名', description_credit: '简介署名', tag_credit: '标签署名',
  library_tag: '标签与已确认歌手', library_title: '标题与已确认歌手',
  title_tag: '标题与标签一致', title_structure: '标题结构', title_body: '标题正文',
  title_phrase: '标题英文片段', title_residual: '标题剩余信息', tag_candidate: '标签候选',
}

export default function MetadataEvidence({ metadata }: { metadata?: MetadataSuggestion | null }) {
  if (!metadata) return null
  return (
    <details className="col-span-2 min-w-0 text-[11px] leading-relaxed text-muted">
      <summary className={`cursor-pointer py-1 ${metadata.needs_review ? 'text-amber-300' : ''}`}>
        {metadata.needs_review ? '待核对 · 查看识别依据' : '查看识别依据'}
      </summary>
      <div className="rounded-lg border border-line bg-bg2 p-2.5 break-words">
        <div>歌名：{sourceNames[metadata.title_source] || '综合标题与标签'} · 演唱者：{sourceNames[metadata.artist_source] || '综合标题与标签'}</div>
        {metadata.artist_confidence < 0.8 && metadata.artist !== '未知歌手' && (
          <div className="text-amber-300">演唱者候选：{metadata.artist}（需核对）</div>
        )}
        {metadata.reasons?.map((reason, i) => <div key={i}>{reason}</div>)}
        {metadata.warnings?.map((warning, i) => <div key={i} className="text-amber-300">{warning}</div>)}
      </div>
    </details>
  )
}

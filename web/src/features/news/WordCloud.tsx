import { ResourceStatus } from '../../components/Common';
import { useResource } from '../../hooks/useResource';
import { wordcloudPath } from './api';
import type { WordCloud, WordCloudGroup, WordCloudResponse, WordCloudWord } from './types';

export interface CloudLayoutItem {
  term: string;
  count: number;
  x: number;
  y: number;
  fontSize: number;
}

export interface CloudLayoutOptions {
  width?: number;
  minFont?: number;
  maxFont?: number;
  lineHeight?: number;
}

// Shared by the layout row pitch and the SVG height estimate so the
// decorative view box can never drift from the laid-out text lines.
const DEFAULT_LINE_HEIGHT = 1.25;

// Wide runs: CJK ideographs/kana/punctuation, Hangul, full-width forms and
// astral emoji/symbols occupy roughly 1em; the tokenizer only emits CJK and
// lowercased Latin/digit runs, but the layout stays deterministic for any
// archived term without measuring the DOM.
function isWideCodePoint(codePoint: number): boolean {
  return (codePoint >= 0x1100 && codePoint <= 0x115f)
    || (codePoint >= 0x2e80 && codePoint <= 0x303e)
    || (codePoint >= 0x3041 && codePoint <= 0x33ff)
    || (codePoint >= 0x3400 && codePoint <= 0x4dbf)
    || (codePoint >= 0x4e00 && codePoint <= 0x9fff)
    || (codePoint >= 0xa000 && codePoint <= 0xa4cf)
    || (codePoint >= 0xac00 && codePoint <= 0xd7a3)
    || (codePoint >= 0xf900 && codePoint <= 0xfaff)
    || (codePoint >= 0xfe30 && codePoint <= 0xfe4f)
    || (codePoint >= 0xff01 && codePoint <= 0xff60)
    || (codePoint >= 0xffe0 && codePoint <= 0xffe6)
    || codePoint >= 0x1f300;
}

function advance(term: string, fontSize: number): number {
  let width = 0;
  for (const character of term) {
    width += isWideCodePoint(character.codePointAt(0) ?? 0) ? 1 : 0.56;
  }
  return width * fontSize;
}

export function cloudLayout(
  words: readonly WordCloudWord[],
  { width = 320, minFont = 13, maxFont = 30, lineHeight = DEFAULT_LINE_HEIGHT }: CloudLayoutOptions = {},
): CloudLayoutItem[] {
  if (words.length === 0) return [];
  let minCount = Infinity;
  let maxCount = -Infinity;
  for (const word of words) {
    if (word.document_count < minCount) minCount = word.document_count;
    if (word.document_count > maxCount) maxCount = word.document_count;
  }
  const span = maxCount - minCount;
  const fontOf = (count: number): number =>
    span === 0 ? minFont : minFont + ((count - minCount) / span) * (maxFont - minFont);

  const items: CloudLayoutItem[] = [];
  let x = 0;
  let y = 0;
  let lineMaxFont = 0;
  words.forEach((word, index) => {
    const fontSize = fontOf(word.document_count);
    const wordWidth = advance(word.term, fontSize);
    if (index > 0) {
      const gap = 0.45 * fontSize;
      if (x > 0 && x + gap + wordWidth > width) {
        y += lineHeight * lineMaxFont;
        x = 0;
        lineMaxFont = 0;
      } else if (x > 0) {
        x += gap;
      }
    }
    items.push({ term: word.term, count: word.document_count, x, y, fontSize });
    x += wordWidth;
    if (fontSize > lineMaxFont) lineMaxFont = fontSize;
  });
  return items;
}

interface WordCloudViewProps {
  data: WordCloud | null;
  selectedTerm: string;
  onSelectTerm: (term: string) => void;
}

export function WordCloudView({ data, selectedTerm, onSelectTerm }: WordCloudViewProps) {
  if (data === null) {
    return <p className="empty wordcloud-empty">当前快照暂无可发布的词云数据。</p>;
  }
  if (data.words.length === 0) {
    return <p className="empty wordcloud-empty">当前范围没有获许可文本可生成词语。</p>;
  }
  const items = cloudLayout(data.words);
  const lastY = items.reduce((max, item) => Math.max(max, item.y), 0);
  const lastLineFont = items.reduce(
    (max, item) => (item.y === lastY ? Math.max(max, item.fontSize) : max),
    0,
  );
  const height = lastY + lastLineFont * DEFAULT_LINE_HEIGHT;
  const { titles, summaries, contents } = data.text_scope;
  return <section className="wordcloud-panel" aria-label="新闻词云">
    <div className="wordcloud-heading">
      <h3>新闻词云</h3>
      <p className="muted wordcloud-meta">
        {data.tokenizer_version} · 标题 {titles} · 摘要 {summaries} · 正文 {contents}
        {' '}· 共 {data.total} 个词
      </p>
    </div>
    <svg
      className="wordcloud-svg"
      viewBox={`0 0 320 ${height}`}
      width="320"
      height={height}
      aria-hidden="true"
    >
      {items.map(item =>
        <text
          key={item.term}
          x={item.x}
          y={item.y + item.fontSize}
          fontSize={item.fontSize}
        >{item.term}</text>)}
    </svg>
    <ul className="wordcloud-words" aria-label="新闻词云词语">
      {data.words.map(word =>
        <li key={word.term}>
          <button
            type="button"
            className="wordcloud-word"
            data-term={word.term}
            aria-pressed={selectedTerm === word.term}
            onClick={() => onSelectTerm(selectedTerm === word.term ? '' : word.term)}
          >
            {word.term}<span className="wordcloud-count">{word.document_count} 篇</span>
          </button>
        </li>)}
    </ul>
  </section>;
}

interface WordCloudProps {
  date: string;
  snapshot: string;
  group: WordCloudGroup;
  topN?: number;
  selectedTerm: string;
  onSelectTerm: (term: string) => void;
  refreshToken?: number;
}

export default function WordCloud({
  date,
  snapshot,
  group,
  topN = 40,
  selectedTerm,
  onSelectTerm,
  refreshToken = 0,
}: WordCloudProps) {
  const resource = useResource<WordCloudResponse>(
    wordcloudPath({ date, snapshot, group, topN }),
    snapshot,
    refreshToken,
  );
  return <>
    <ResourceStatus resource={resource} name="新闻词云" />
    {resource.data !== undefined &&
      <WordCloudView
        data={resource.data.data}
        selectedTerm={selectedTerm}
        onSelectTerm={onSelectTerm}
      />}
  </>;
}

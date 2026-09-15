import type { NoteContent } from './types';
import { codepoints } from './draft';
import type { ContentField, ContentFieldErrors } from './validation';

export function ContentFields({ value, onChange, disabled, prefix = '', idPrefix = 'note-content', errors = {}, fieldRef }: {
  value: NoteContent; onChange: (content: NoteContent) => void; disabled?: boolean; prefix?: string;
  idPrefix?: string; errors?: ContentFieldErrors; fieldRef?: (field: ContentField, element: HTMLInputElement | HTMLTextAreaElement | null) => void;
}) {
  const errorProps = (field: ContentField) => ({
    'aria-invalid': errors[field]?.length ? true : undefined,
    'aria-describedby': errors[field]?.length ? `${idPrefix}-${field}-error` : undefined,
  });
  const fieldError = (field: ContentField) => errors[field]?.length
    ? <span className="field-error" id={`${idPrefix}-${field}-error`}>{errors[field]!.join(' ')}</span>
    : null;
  return <fieldset className="review-fields" disabled={disabled}>
    <legend className="sr-only">{prefix || '编辑'}复盘正文</legend>
    <label>{prefix}标题<input ref={element => fieldRef?.('title', element)} aria-label={`${prefix}标题`} {...errorProps('title')}
      value={value.title} onChange={event => onChange({ ...value, title: event.target.value })} />{fieldError('title')}</label>
    <span className="muted">标题 {codepoints(value.title)} / 100 码点</span>
    {([['judgment', '判断'], ['evidence', '依据'], ['invalidation', '失效条件']] as const).map(([key, label]) =>
      <label key={key}>{prefix}{label}<textarea ref={element => fieldRef?.(key, element)} aria-label={`${prefix}${label}`} {...errorProps(key)}
        rows={4} value={value[key]} onChange={event => onChange({ ...value, [key]: event.target.value })} />{fieldError(key)}</label>)}
    <span className="muted">三正文合计 {codepoints(value.judgment + value.evidence + value.invalidation)} / 20000 码点；纯文本，不执行标记或脚本。</span>
    <label>{prefix}验证日期<input ref={element => fieldRef?.('verification_date', element)} aria-label={`${prefix}验证日期`}
      {...errorProps('verification_date')} type="date" value={value.verification_date || ''}
      onChange={event => onChange({ ...value, verification_date: event.target.value || null })} />{fieldError('verification_date')}</label>
  </fieldset>;
}
export function ContentText({ content }: { content: NoteContent }) {
  return <dl className="review-text">
    {([['title', '标题'], ['judgment', '判断'], ['evidence', '依据'], ['invalidation', '失效条件'], ['verification_date', '验证日期']] as const).map(([key, label]) =>
      <div key={key}><dt>{label}</dt><dd>{content[key] || '未填写'}</dd></div>)}
  </dl>;
}
export function PrivatePager({ page, size, more, change, name }: {
  page: number; size: number; more: boolean; change: (page: number, size: number) => void; name: string;
}) {
  return <nav className="pager" aria-label={`${name}分页`}>
    <span>第 {page} 页 · 并发变更后请刷新列表</span>
    <label>每页<select aria-label={`${name}每页条数`} value={size} onChange={event => change(1, +event.target.value)}>
      {[20, 50, 100].map(value => <option key={value}>{value}</option>)}
    </select></label>
    <button disabled={page <= 1} onClick={() => change(page - 1, size)}>上一页</button>
    <button disabled={!more || page >= 100000} onClick={() => change(page + 1, size)}>下一页</button>
  </nav>;
}
export function ReadStatus({ loading, error, retry }: { loading: boolean; error?: string; retry: () => void }) {
  return <>{loading && <p role="status">正在读取私人记录…</p>}{error && <div className="notice" role="alert">{error}<button onClick={retry}>重新读取</button></div>}</>;
}

export function ModuleState({ reason, missing }: { reason: string; missing: string[] }) {
  return <section className="module-state" role="status" aria-label="模块数据状态">
    <h2>未就绪</h2>
    <p>{reason}</p>
    <ul>{missing.map(item => <li key={item}>{item}</li>)}</ul>
    <p className="muted">缺失不等于零；当前不展示示例证券或推断结果。</p>
  </section>;
}

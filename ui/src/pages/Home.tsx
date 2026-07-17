export type Offering = 'process' | 'diagram'

export default function HomePage(props: { onPick: (o: Offering) => void }) {
  return (
    <>
      <div className="genq-hero">
        <h1>Gen<span>Q</span></h1>
        <p className="genq-caption">Gentic Mining</p>
      </div>
      <p className="lead">
        Two apps share this GenQ workspace — agent-built process mining and BPMN diagram
        intelligence. Switch between them anytime from the sidebar.
      </p>
      <div className="offerings">
        <button className="offering-card" onClick={() => props.onPick('process')}>
          <div className="offering-icon">P</div>
          <h2>
            Process<span>IQ</span>
          </h2>
          <p>
            Process mining from your source tables. The AI agent builds the event log, you
            confirm it once, then dashboards, variant explorer, KPIs and a co-pilot are
            self-serve.
          </p>
          <ul>
            <li>Agent-drafted data model &amp; transformations</li>
            <li>Process &amp; variant explorer, insights, custom KPIs</li>
            <li>Scheduled loads, filters, NL co-pilot</li>
          </ul>
          <span className="offering-cta">Open ProcessIQ</span>
        </button>

        <button className="offering-card" onClick={() => props.onPick('diagram')}>
          <div className="offering-icon">D</div>
          <h2>
            Diagram<span>IQ</span>
          </h2>
          <p>
            BPMN 2.0 diagram intelligence. Turn meeting notes or a process Excel into
            Signavio-ready BPMN, validate it against best-practice rules, and uplift it —
            no modeller required to start.
          </p>
          <ul>
            <li>Notes / transcript → Process Discovery Excel</li>
            <li>Excel → BPMN 2.0 XML (Signavio-ready) + viewer</li>
            <li>Validation report, rule-based uplift, Excel round-trip</li>
          </ul>
          <span className="offering-cta">Open DiagramIQ</span>
        </button>
      </div>
    </>
  )
}

import { useState } from 'react'
import { clearWorkspace, getApiKey, getWorkspaceName } from './api'
import Copilot from './components/Copilot'
import WorkspacePage from './pages/Workspace'
import HomePage, { Offering } from './pages/Home'
import DataPage from './pages/Data'
import MappingPage from './pages/Mapping'
import DataLoadPage from './pages/DataLoad'
import DashboardPage from './pages/Dashboard'
import ImprovePage from './pages/Improve'
import AskPage from './pages/Ask'
import DiagramStudioPage from './pages/DiagramStudio'
import MembersPage from './pages/Members'
import DictionaryPage from './pages/Dictionary'
import DiagramReportPage from './pages/DiagramReport'
import WorkflowAutomationPage from './pages/WorkflowAutomation'

export type Tab =
  | 'home' | 'data' | 'mapping' | 'dataload' | 'dashboard' | 'improve' | 'ask' | 'studio'
  | 'members' | 'dictionary' | 'diagramreport' | 'automation'

const PROCESS_STEPS: { id: Tab; label: string }[] = [
  { id: 'data', label: 'Data' },
  { id: 'mapping', label: 'Mapping review' },
  { id: 'dataload', label: 'Data load' },
  { id: 'dashboard', label: 'Dashboard' },
  { id: 'improve', label: 'Improve' },
  { id: 'ask', label: 'Ask' },
  { id: 'members', label: 'Members' },
]

const DIAGRAM_STEPS: { id: Tab; label: string }[] = [
  { id: 'studio', label: 'Studio' },
  { id: 'dictionary', label: 'Dictionary' },
  { id: 'automation', label: 'Automation' },
  { id: 'diagramreport', label: 'Reports' },
  { id: 'members', label: 'Members' },
]

// pages where the co-pilot dock is available (ProcessIQ offering)
const COPILOT_TABS: Tab[] = ['data', 'mapping', 'dataload', 'dashboard', 'improve']

export default function App() {
  const [connected, setConnected] = useState(!!getApiKey())
  const [offering, setOffering] = useState<Offering | null>(
    (localStorage.getItem('processiq_offering') as Offering | null) ?? null,
  )
  const [tab, setTab] = useState<Tab>(offering === 'diagram' ? 'studio' : 'data')
  const [copilotContext, setCopilotContext] = useState<string | null>(null)

  const pickOffering = (o: Offering | null) => {
    setOffering(o)
    if (o) localStorage.setItem('processiq_offering', o)
    else localStorage.removeItem('processiq_offering')
    setTab(o === 'diagram' ? 'studio' : 'data')
    setCopilotContext(null)
  }

  const switchTab = (t: Tab) => {
    setTab(t)
    if (t !== 'dataload') setCopilotContext(null)
  }

  if (!connected) {
    return (
      <>
        <Sidebar offering={null} tab={tab} setTab={switchTab} onHome={() => {}} disabled
                 onSignOut={() => {}} />
        <main>
          <WorkspacePage onReady={() => setConnected(true)} />
        </main>
      </>
    )
  }

  if (!offering) {
    return (
      <>
        <Sidebar offering={null} tab={tab} setTab={switchTab} onHome={() => {}} disabled
                 onSignOut={() => {
                   clearWorkspace()
                   setConnected(false)
                 }} connectedFoot />
        <main>
          <HomePage onPick={pickOffering} />
        </main>
      </>
    )
  }

  const showCopilot = offering === 'process' && COPILOT_TABS.includes(tab)

  return (
    <>
      <Sidebar
        offering={offering}
        tab={tab}
        setTab={switchTab}
        onHome={() => pickOffering(null)}
        onSignOut={() => {
          clearWorkspace()
          setConnected(false)
          pickOffering(null)
        }}
        connectedFoot
      />
      <main className={showCopilot ? 'with-copilot' : ''}>
        {offering === 'process' && (
          <>
            {tab === 'data' && <DataPage onContinue={() => switchTab('mapping')} />}
            {tab === 'mapping' && <MappingPage onMaterialized={() => switchTab('dataload')} />}
            {tab === 'dataload' && (
              <DataLoadPage
                selected={copilotContext}
                onSelect={setCopilotContext}
                onEditModel={() => switchTab('mapping')}
              />
            )}
            {tab === 'dashboard' && <DashboardPage />}
            {tab === 'improve' && <ImprovePage />}
            {tab === 'ask' && <AskPage />}
          </>
        )}
        {offering === 'diagram' && tab === 'studio' && <DiagramStudioPage />}
        {offering === 'diagram' && tab === 'dictionary' && <DictionaryPage />}
        {offering === 'diagram' && tab === 'automation' && <WorkflowAutomationPage />}
        {offering === 'diagram' && tab === 'diagramreport' && <DiagramReportPage />}
        {tab === 'members' && <MembersPage />}
      </main>
      {showCopilot && (
        <Copilot
          context={copilotContext}
          onClearContext={() => setCopilotContext(null)}
          page={tab}
        />
      )}
    </>
  )
}

function Sidebar(props: {
  offering: Offering | null
  tab: Tab
  setTab: (t: Tab) => void
  onHome: () => void
  disabled?: boolean
  onSignOut: () => void
  connectedFoot?: boolean
}) {
  const steps =
    props.offering === 'diagram' ? DIAGRAM_STEPS : props.offering === 'process' ? PROCESS_STEPS : []
  return (
    <nav className="sidebar">
      <div className="brand">
        Gen<span>Q</span>
        <div className="brand-caption">Gentic Mining</div>
      </div>
      {props.offering && (
        <div className="brand-app">
          {props.offering === 'diagram' ? 'DiagramIQ' : 'ProcessIQ'}
        </div>
      )}
      {props.offering && (
        <button className="nav" onClick={props.onHome}>
          <span className="step-no">‹</span> Home
        </button>
      )}
      {steps.map((step, i) => (
        <button
          key={step.id}
          className={'nav' + (props.tab === step.id && !props.disabled ? ' active' : '')}
          disabled={props.disabled}
          onClick={() => props.setTab(step.id)}
        >
          <span className="step-no">{i + 1}</span> {step.label}
        </button>
      ))}
      <div className="foot">
        {props.connectedFoot || !props.disabled ? (
          <>
            {getWorkspaceName() || 'Workspace'} · <button onClick={props.onSignOut}>switch</button>
          </>
        ) : (
          'No workspace connected'
        )}
      </div>
    </nav>
  )
}

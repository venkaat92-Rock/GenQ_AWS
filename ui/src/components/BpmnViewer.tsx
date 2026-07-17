import { useEffect, useRef } from 'react'
// @ts-ignore — bpmn-js ships its own types under a nested path
import NavigatedViewer from 'bpmn-js/lib/NavigatedViewer'
import 'bpmn-js/dist/assets/bpmn-js.css'
import 'bpmn-js/dist/assets/diagram-js.css'
import 'bpmn-js/dist/assets/bpmn-font/css/bpmn.css'

export default function BpmnViewer(props: { xml: string }) {
  const host = useRef<HTMLDivElement>(null)
  const viewer = useRef<any>(null)

  useEffect(() => {
    if (!host.current) return
    if (!viewer.current) {
      viewer.current = new NavigatedViewer({ container: host.current })
    }
    viewer.current
      .importXML(props.xml)
      .then(() => viewer.current.get('canvas').zoom('fit-viewport'))
      .catch(() => {})
    return () => {}
  }, [props.xml])

  useEffect(() => () => viewer.current?.destroy(), [])

  return <div ref={host} className="bpmn-canvas" />
}

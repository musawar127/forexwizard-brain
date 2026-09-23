"use client";
import { useEffect,useState } from "react";
import { API } from "@/lib/api";
import type { ResearchItem } from "@/lib/types";

export default function ResearchPage(){
 const [items,setItems]=useState<ResearchItem[]>([]); const [status,setStatus]=useState("STARTING");
 useEffect(()=>{const run=()=>fetch(`${API}/api/research?limit=50`,{cache:"no-store"}).then(r=>r.json()).then(x=>{setItems(x.items||[]);setStatus(x.status||"UNKNOWN")}).catch(()=>setStatus("OFFLINE")); run(); const t=setInterval(run,30000); return()=>clearInterval(t)},[]);
 return <><div className="page-title"><div><span>RESEARCH BRAIN</span><h2>Gold & macro discovery</h2></div><p>The no-key collector stores recent public article metadata from GDELT. It never treats headlines as proof of market direction.</p></div>
 <section className="panel"><div className="panel-head"><div><div className="panel-kicker">DISCOVERY FEED</div><h2>Recent research items</h2></div><span className="mini-chip">{status}</span></div>
 <div className="research-list">{items.length?items.map(item=><a href={item.url} target="_blank" rel="noreferrer" className="research-item" key={item.id}><div><strong>{item.title}</strong><span>{item.domain} · {item.source_country||"Unknown country"}</span></div><b>↗</b></a>):<div className="empty-state">No research stored yet. Leave the backend running and the collector will retry automatically.</div>}</div></section></>;
}

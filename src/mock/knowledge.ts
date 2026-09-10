import type { KnowledgeDoc, DocKind } from '@/types';

type R = [string, DocKind, string, string, string, string, string, number, boolean, string[], string, string[]];
const rows: R[] = [
  ['REQ-238','requirement','Invoice tax correctness — interstate supply','erp','Email from Finance · 2026-08-21','21 d ago','14 KB',9,true,['CGST','SGST','IGST','place of supply','MST_TAX'],'Finance flagged that interstate invoices show two tax heads instead of one. Names 41 affected invoices and the August filing window as the deadline.',['TASK-492','BUG-883','ADR-52']],
  ['REQ-291','requirement','Customer bulk upload from CSV','erp','WhatsApp voice note, transcribed','2 d ago','6 KB',4,true,['CSV','50k rows','rollback','validation'],'Operator wants to import up to 50,000 customers with per-row errors visible before commit, and a guarantee that a mid-file failure changes nothing.',['TASK-488','ADR-49']],
  ['MTG-114','meeting','Billing review — 2026-08-21','erp','Google Meet transcript · 38 min','21 d ago','82 KB',34,true,['rounding','place of supply','SEZ','filing'],'Seven facts extracted, including that SEZ supplies were never specified and the team has been guessing since 2021.',['MEM-311','MEM-402','ADR-47']],
  ['MTG-118','meeting','HIMS renewal kickoff','hims','Zoom transcript · 52 min','5 d ago','104 KB',41,true,['PMI schema','OPD','IPD','compatibility'],'Established that the legacy PMI schema is a hard compatibility contract for the whole renewal.',['TASK-503']],
  ['SS-402','screenshot','Invoice showing CGST+SGST on an interstate order','erp','Operator screenshot · INV-2026-08812','21 d ago','412 KB',1,true,['InvoiceTaxSummary','tax block'],'Vision agent matched the tax block to InvoiceTaxSummary.tsx with confidence 0.91 — the entry point for the whole TASK-492 investigation.',['BUG-883','InvoiceTaxSummary.tsx']],
  ['SS-418','screenshot','Excel export timeout error dialog','erp','Operator screenshot','2 d ago','188 KB',1,true,['ExcelExport','timeout'],'Error text matched to ExcelExport.ts buffering path. Became BUG-991.',['BUG-991','TASK-513']],
  ['PDF-77','pdf','GST place-of-supply rules — CBIC circular 183/15/2022','erp','cbic.gov.in','4 mo ago','1.2 MB',148,true,['place of supply','IGST','SEZ','union territory'],'The statutory basis for resolving jurisdiction. Cited directly by ADR-52 — the rule the code was getting wrong.',['ADR-52','MEM-311']],
  ['PDF-81','pdf','SQL Server 2019 session state options','erp','Microsoft Learn export','5 h ago','640 KB',72,true,['session state','Redis','InProc','app pool'],'Read during TASK-501. Establishes that InProc cannot survive a recycle, which closed the investigation.',['TASK-501','ADR-50']],
  ['SPEC-19','spec','CareWorks ERP module renewal standard','erp','Internal · v4','2 mo ago','96 KB',58,true,['SOLID','naming','repository','interface-first'],'The written source for the ten project rules the OS enforces automatically.',['project rules']],
  ['SPEC-24','spec','Bulk import contract','erp','ADR-49 companion','yesterday','22 KB',11,true,['staging table','transaction','partial failure'],'Defines staging-then-commit as the pattern for every bulk import, not just customers.',['ADR-49','TASK-488']],
  ['BUG-883','ticket','CGST/SGST reversed on interstate orders','erp','Internal tracker','21 d ago','8 KB',5,true,['tax','interstate','41 invoices'],'The original bug report. 41 invoices, ₹2.14L of misallocated tax across two states.',['TASK-492','REQ-238']],
  ['BUG-991','ticket','Excel export times out past 100k rows','erp','Internal tracker','2 d ago','4 KB',3,true,['export','timeout','memory'],'Reproduced at 104k rows. Root cause is full in-memory buffering.',['TASK-513','ExcelExport.ts']],
  ['BUG-905','ticket','Users logged out after ~20 minutes','erp','Internal tracker','1 d ago','12 KB',7,true,['session','IIS','app pool'],'214 forced logouts correlated to app-pool recycles within 4 seconds.',['TASK-501']],
  ['EM-31','email','Statutory filing deadline — September','erp','finance@careworks · 2026-09-02','8 d ago','3 KB',2,true,['GSTR-1','deadline','filing window'],'Sets the constraint that makes the TASK-492 migration a HIGH-risk deploy: it lands inside the filing window.',['APPR-119']],
  ['EM-34','email','Approval policy for production changes','erp','you@sofscript · 2026-06-14','3 mo ago','2 KB',1,true,['approval','production','signature'],'The written origin of the rule that production always carries a human signature.',['permission rule p13']],
  ['GIT-a82','git','fix(tax): resolve jurisdiction by place of supply','erp','a82f91c · Backend Engineer','12 min ago','18 KB',6,true,['TaxService','ITaxJurisdictionResolver'],'The commit implementing ADR-52. Diff indexed so the reviewer can cite exact lines.',['TASK-492','ADR-52']],
  ['GIT-4d0','git','test(tax): interstate matrix','erp','4d0e17b · QA Engineer','8 min ago','12 KB',4,true,['JurisdictionTests','SEZ'],'Eighteen cases covering every jurisdiction combination the circular describes.',['TASK-492']],
  ['SCH-01','schema','CareWorks ERP schema snapshot','erp','SQL Server 2019 · nightly','today 07:02','14 MB',1_842,true,['382 tables','1148 procedures','audit triggers'],'The parsed schema behind every database claim the OS makes. Refreshed nightly.',['MST_TAX','TRANS_INVOICE','SP_CalculateTax']],
  ['SCH-02','schema','HIMS PMI schema snapshot','hims','SQL Server · nightly','today 07:04','6 MB',908,true,['214 tables','640 procedures'],'The compatibility contract for the HIMS renewal.',['TASK-503']],
  ['TR-08','transcript','Support call — customer disputes an invoice total','erp','Call recording, transcribed','19 d ago','28 KB',14,true,['dispute','tax','rounding'],'The customer-facing consequence of BUG-883 — useful context for why Finance escalated.',['BUG-883']],
  ['URL-12','url','Qwen3-Coder-Next model card','aios','qwen.ai','6 d ago','44 KB',18,true,['256K context','agentic coding'],'Basis for routing all coding work to local weights.',['model router']],
  ['URL-14','url','Agent Client Protocol specification','aios','agentclientprotocol.com','1 d ago','88 KB',36,true,['ACP','JSON-RPC','session lifecycle'],'The spec TASK-521 is implementing against.',['TASK-521']],
  ['DOC-05','pdf','Playwright visual comparison guide','erp','playwright.dev export','1 w ago','210 KB',31,true,['screenshot','baseline','pixel diff'],'Used to set the visual-regression threshold at 0.2%.',['visual suite']],
  ['MTG-121','meeting','Standup — 2026-09-09','erp','Notes','yesterday','8 KB',6,true,['bulk upload','papaparse','blocked'],'Where the papaparse dependency question first came up.',['APPR-120','TASK-488']],
  ['TR-11','transcript','Architecture walkthrough — GL posting engine','erp','Screen recording, transcribed','4 mo ago','62 KB',28,true,['PostingEngine','double entry','invariant'],'The only recorded explanation of how the GL balance invariant works. The engineer who wrote it has left.',['PostingEngine.cs','BUG-311']],
  ['REQ-304','requirement','Surge pricing — per zone, capped','taxi','Product doc','today','9 KB',5,true,['surge','H3','cap 2.5x'],'Written after RES-77 landed. Sets the 2.5x ceiling as a product decision, not a technical one.',['TASK-506','ADR-07']],
  ['SPEC-31','spec','Money handling contract','hims','Internal','3 d ago','16 KB',8,true,['rounding','half away from zero','2dp'],'Written after the OPD rounding mismatch was found, to stop it recurring.',['TASK-503']],
  ['EM-38','email','Vendor confirmation — E-Way Bill API v2 migration','erp','nic.gov.in','3 w ago','5 KB',3,false,['e-way bill','API v2','deadline'],'Not yet indexed — flagged for review because it implies a contract change with a fixed deadline.',['DispatchService']],
];

export const knowledgeDocs: KnowledgeDoc[] = rows.map(([ref, kind, title, projectId, source, addedAt, size, chunks, indexed, entities, summary, linkedTo], i) => ({
  id: `k${i}`, ref, kind, title, projectId, source, addedAt, size, chunks, indexed, entities, summary, linkedTo,
}));

export const docKinds: DocKind[] = ['requirement', 'meeting', 'screenshot', 'pdf', 'spec', 'ticket', 'email', 'git', 'schema', 'transcript', 'url'];

export const retrievalDemo = {
  query: 'customer invoice tax',
  results: [
    { ref: 'REQ-238', title: 'Invoice tax correctness — interstate supply', score: 0.94, via: 'vector', why: 'Semantic match on "invoice tax" plus entity overlap on MST_TAX.' },
    { ref: 'MTG-114', title: 'Meeting 2026-08-21 — Billing review', score: 0.89, via: 'vector', why: 'Transcript chunk discussing place of supply and rounding.' },
    { ref: 'BUG-991', title: 'Excel export times out past 100k rows', score: 0.71, via: 'keyword', why: 'Literal "invoice" in the report title — kept, but ranked down by the reranker.' },
    { ref: 'SP_CalculateTax', title: 'Stored procedure · 412 lines', score: 0.92, via: 'graph', why: 'Reached through MST_TAX from the entity graph, not by text similarity.' },
    { ref: 'InvoiceService.cs', title: 'Backend service · 412 LOC', score: 0.88, via: 'graph', why: 'One hop from SP_CalculateTax in the call graph.' },
    { ref: 'ADR-42', title: 'One repository per aggregate', score: 0.64, via: 'vector', why: 'Weak semantic match. Surfaced because it constrains how the fix may be written.' },
  ],
  retrievers: [
    { name: 'Vector · BGE-M3', hits: 42, note: 'Dense retrieval over 1,842 chunks. Good at paraphrase, blind to exact identifiers.' },
    { name: 'Keyword · BM25', hits: 18, note: 'Catches identifiers a vector misses — SP_CalculateTax, MST_TAX, INV-2026-08812.' },
    { name: 'Graph · entity walk', hits: 11, note: 'Follows parsed relationships. Finds code a text search would never rank.' },
    { name: 'Reranker · BGE-Reranker-v2', hits: 6, note: 'Re-scores the union of the three and keeps what actually answers the question.' },
  ],
};

export const ingestPipeline = [
  { n: 1, step: 'Classify', detail: 'Kind detected from MIME, structure and content — a transcript is not a spec.' },
  { n: 2, step: 'Extract facts', detail: 'Durable claims are pulled out with their reason and source, then offered to memory.' },
  { n: 3, step: 'Identify entities', detail: 'Tables, procedures, classes, refs and people are linked to the parsed graph.' },
  { n: 4, step: 'Link to project', detail: 'Scoped to one project. Nothing leaks between them except the global brain.' },
  { n: 5, step: 'Chunk & embed', detail: 'Semantic chunking, BGE-M3 at 1024 dimensions, stored in Qdrant.' },
  { n: 6, step: 'Index', detail: 'Vector, keyword and graph indexes updated together so retrieval stays consistent.' },
];

export const accepted = ['PDF', 'DOCX', 'TXT', 'Markdown', 'Screenshot', 'Meeting transcript', 'Chat export', 'Git commit', 'GitHub issue', 'Jira ticket', 'Email', 'URL', 'Database schema', 'Source code'];

# Wright Foster Group LLC Government Contracting Operating System

Business name: Wright Foster Group LLC
Legal entity state: Maryland
Domain: wrightfostergroup.com

Status: legal name and domain are locked in based on user-confirmed availability. LLC registration and domain purchase are being handled this week.

Purpose: build a broad prime-contractor business that finds government opportunities, sources qualified subcontractors near the place of performance, marks up pricing responsibly, and submits compliant bids.

Ownership/control plan: structure Wright Foster Group LLC with the female veteran partner controlling 51% and Nick controlling 49%, with the 51% owner holding real ownership, management/control, long-term decision authority, and approval authority. This may support eligibility for women-owned and veteran-owned small business opportunities if all SBA/agency certification requirements are met. Do not claim WOSB, EDWOSB, VOSB, SDVOSB, or other set-aside/certification status until eligibility and certification are verified.

Operating architecture: Marcus is the main conversational operations assistant. Marcus should stay available to Nick for direction, questions, approvals, and summaries. Specialized work should be delegated to focused WFG subagents that each load the skill or skill bundle needed for the task. Skills define the procedures; subagents execute those procedures in isolated contexts and return concise outputs for Marcus to verify and explain.

Starter strategy (current stage): target simplified-acquisition work ($10K-$250K, hard cap $500K) in facility services and light trades; respond to sources-sought notices weekly to build agency relationships; pre-build a validated subcontractor bench (templates/sub_bench_plan.md) before bidding so 7-14 day RFQs are winnable; record every award, however small, as past performance. Search behavior is governed by opportunity-searches/sam_search_profile.json.

Default model:
1. Hunt for SAM.gov opportunities.
2. Capture solicitation link, documents, deadlines, NAICS/PSC, place of performance, set-aside, and submission method.
3. Delegate bid/no-bid scoring to a WFG capture/strategy subagent.
4. Delegate solicitation reading and compliance-matrix extraction to a WFG reader/compliance subagent.
5. Delegate subcontractor sourcing, outreach drafting, and credential checks to sourcing/validator subagents.
6. Delegate pricing and limitations-on-subcontracting analysis to pricing/LOS subagents.
7. Delegate proposal assembly to a proposal compiler subagent.
8. Delegate final compliance/red-team review to an auditor subagent.
9. Marcus holds the human approval gate, explains the state of work, and never allows final submission without explicit authorized-owner approval.

Working rule: do not claim certifications, bonding, licensing, insurance, past performance, or subcontractor commitments unless verified.

Hermes tracking:
- Board slug: gov-contracting
- Board display name: Wright Foster Group LLC
- Default workspace: /home/nick/workspace/wfg-gov-contracting-v2
- Main skill: sam-gov-contracting-system
- WFG imported skills: /home/nick/.hermes/skills/business-ops/
- Imported architecture source: /home/nick/workspace/wfg-gov-contracting-v2/imports/complete-hermes-architecture-20260520/

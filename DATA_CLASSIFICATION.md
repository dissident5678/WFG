# WFG Data Classification

Class 0 - Public: SAM opportunity pages, public solicitation PDFs, public agency info. May be summarized in chat.

Class 1 - Business Sensitive: pricing assumptions, bid strategy, proposal drafts, internal rates, subcontractor strategy. Store in the workspace; chat should reference IDs/summaries rather than dumping sensitive content.

Class 2 - Vendor PII / Compliance Docs: W-9s, COIs, licenses, tax IDs, personal addresses, banking details. Store only in an approved controlled location. Do not transmit in Telegram or casual chat.

Class 3 - CUI / Controlled Contract Data: CUI-marked attachments, controlled drawings, technical data, export-controlled data, or suspected controlled contract information. Do not process through ordinary external LLMs, Telegram, or cloud tools. Stop and ask for human handling instructions.

Class 4 - Legal / Privileged: attorney advice, disputes, protests, privileged communications. Keep separate and access-restricted.

Subagent rule: every subagent must classify the files it touches. If data appears Class 2, 3, or 4, the subagent must flag it and avoid unsafe routing.

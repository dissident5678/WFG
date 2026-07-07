# Subcontractor Bid Packet Templates

This folder is authoritative for WFG subcontractor bid packets.

- `Hermes_Subcontractor_Bid_Packet_Instructions.docx` gives the human-readable instruction set for Hermes/agents.
- `WFG_Subcontractor_Bid_Packet_Template.docx` is the dynamic DOCX template used by `scripts/wfg_sub_bid_packet.py`.

Template syntax:

```text
{{field_name}}              replace with a field
[[IF condition]]...[[/IF]]  include only if condition is true
[[IF_NOT condition]]...     include only if condition is false
[[REPEAT list]]...          repeat table rows or paragraphs for each list item
```

Do not send the template itself to subcontractors. Send only a generated `subcontractor_bid_packet.docx` after Gate 2 approval.

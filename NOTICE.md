# Provenance

Orchestra AI is an independent, clean-room implementation.

It was **conceptually inspired** by [salmanmkc/agentverse](https://github.com/salmanmkc/agentverse),
a hackathon project by its original contributors that explored AI-assisted task breakdown,
matching work to team members, and pushing the results to GitHub. Credit for that original
concept and implementation belongs to its authors.

No source code, assets, data, or model weights were copied from that repository:

- The agentverse repository has no license at its root, so its code is not licensed for reuse
  and was used only as a reference for publicly visible ideas.
- Its `mcp-backend/` directory is a copy of CNOE's
  [ai-platform-engineering](https://github.com/cnoe-io/ai-platform-engineering) (Apache-2.0).
  Orchestra AI does not include or derive from that code either.
- The agentverse repository also contains credential files and personal email-derived datasets;
  none of that material is used or redistributed here.

Orchestra AI's product scope (an engineering-operations control plane built on LangGraph with
human approval gates), its architecture, and all of its code were written for this project.

Third-party dependencies and their licenses are listed in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

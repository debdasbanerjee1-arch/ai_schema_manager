Yes, generate the design and the tasks, in this order, stopping after each for my approval:

1. **Requirements first.** I'm providing two files that replace yours completely:
   - `.kiro/steering/knowledge-fabric.md`
   - `requirements.md`

   Put `requirements.md` wherever your spec keeps it. Read both in full.
   - If anything in either is ambiguous, or conflicts with the repository as it stands, list it and stop.
   - Don't edit either file. Propose any change instead.
   - Don't restate the requirements back to me.

2. **Design, once you've confirmed you've read both files.**
   - The steering file is the authority. The design doc refers to it and doesn't restate or change its rules. If the design needs a rule changed, list the change as a proposal at the top.
   - Cover:
     - the package layout
     - the module for each harvester, importer and resolver rule
     - the inventory reader and writer
     - redaction
     - snapshot selection
     - how the resolver gets inputs to each rule and collects nodes, edges and gaps
     - the `CURRENT` graph switch
     - the definition batch and ingest commands
     - the test approach
   - Give every requirement number the design covers.
   - For every existing module, say whether it is kept, changed, moved or deleted.
   - Keep it short: interfaces and data flow, not code.

3. **Tasks, after I approve the design.** Group them into the stages of `inventory-restructure-prompt.md`, in the same order:
   - Stage 0: freeze and baseline
   - Stage 1: inventory foundation, with no live AWS calls
   - Stage 2: resolver v1 and the comparison with the legacy graph
   - Stages 3 to 7: listed only
   - Each task names its requirement numbers and its tests.
   - Put a checkpoint task at the end of each stage: "stop and show the user …".
   - No task makes a live AWS call without its own approval step.

Then build only stage 0, and stop.

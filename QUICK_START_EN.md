# Quick start

A one-page summary to get going. The details of each function (bridges, recipes, boards, machine and tool profiles, hold-down, time estimate…) are in the **user manual**, `Manual_cnc_app.docx`.

## What you need

`cnc_app.exe`, a single file, and your data files next to it: `herramientas.json` (tools), `maquinas.json` (machines), `cnc_app.toml` (default settings) and `receta_ejemplo.toml`. Without `maquinas.json` nothing is generated. Double-click to open it; copy the folder as is to another PC.

## A job in five steps

1. **Open an SVG** (File → Open SVG drawing…, or drag it onto the window) and tick the boards you want to generate.
2. **Choose what to do with each color** in the table below: Cut, Pocket, Drill or Ignore.
3. **Set the tool and the thickness** of the material (and the machine, if there is more than one).
4. **Check** the *Machine* view and the *Messages* tab. If the job does not fit or breaks a limit, it is not generated and the summary says why.
5. **Generate the G-code**: it writes one `.nc` per board. Sending it, homing and setting zero are still done in UGS.

## Conventions

Units are mm. Z0 is the top face of the material, and through-cuts go down to -(thickness + margin). (0,0) is the bottom-left corner of the board. Holes are cut first and then the parts, always in that order.

## Language

The window starts in the language of Windows (Spanish or English); change it in *Help → Language*.

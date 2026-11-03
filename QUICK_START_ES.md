# Quick start

Resumen de una página para empezar. El detalle de cada función (puentes, recetas, tableros, fichas, sujeción, tiempos…) está en el **manual de usuario**, [`Manual_cnc_app_ES.pdf`](docs/Manual_cnc_app_ES.pdf).

## Qué necesitas

`cnc_app.exe`, un solo archivo, y junto a él tus datos: `herramientas.json` (fresas), `maquinas.json` (máquinas), `cnc_app.toml` (valores por defecto) y `receta_ejemplo.toml`. Sin `maquinas.json` no se genera nada. Se abre con doble clic y se copia tal cual a otro PC.

## Un trabajo en cinco pasos

1. **Abre un SVG** (Archivo → Abrir dibujo SVG, o arrástralo a la ventana) y marca los tableros que quieres generar.
2. **Elige qué hacer con cada color** en la tabla de abajo: Corte, Vaciado, Taladro o Ignorar.
3. **Pon la fresa y el grosor** del material (y la máquina, si hay más de una).
4. **Revisa** la vista *Máquina* y la pestaña *Mensajes*. Si el trabajo no cabe o rompe un tope, no se genera y el resumen dice por qué.
5. **Genera el G-code**: escribe un `.nc` por tablero. Enviarlo, hacer Home y poner el cero sigue siendo cosa de UGS.

## Convenciones

Medidas en mm. Z0 en la cara superior del material y los cortes pasantes bajan hasta -(grosor + margen). El (0,0) es la esquina inferior izquierda del tablero. Se cortan primero los huecos y después las piezas, siempre en ese orden.

## Idioma

La ventana arranca en el idioma de Windows (español o inglés); se cambia en *Ayuda → Idioma*.

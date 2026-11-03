@echo off
rem Genera dist_onefile\cnc_app.exe: UN SOLO archivo autocontenido (se descomprime en %TEMP% en cada arranque).
rem Los archivos que se editan a mano (herramientas.json, maquinas.json, cnc_app.toml, recetas) NO van dentro: se copian
rem junto al exe, que es donde los busca el programa. Hace falta Python 3.11+. Ejecutar desde la carpeta del programa.

tasklist /FI "IMAGENAME eq cnc_app.exe" 2>nul | "%SystemRoot%\System32\find.exe" /I "cnc_app.exe" >nul
if not errorlevel 1 (
    echo.
    echo cnc_app.exe esta abierto: cierralo y vuelve a lanzar este script.
    goto :error
)

echo [1/4] Instalando dependencias...
python -m pip install -r requirements-dev.txt || goto :error

rem Lo editado junto al exe (p. ej. las fichas con el editor) se trae aqui antes si es mas reciente, para no pisarlo.
echo [2/4] Guardando lo editado en dist_onefile...
for %%F in (herramientas.json maquinas.json cnc_app.toml receta_ejemplo.toml) do if exist "dist_onefile\%%F" xcopy /D /Y "dist_onefile\%%F" . >nul

echo [3/4] Compilando con PyInstaller en un solo archivo (tarda unos minutos)...
python -m PyInstaller --noconfirm --clean --distpath dist_onefile --workpath build_onefile cnc_gui.spec || goto :error
if not exist dist_onefile\cnc_app.exe (
    echo PyInstaller no ha dejado dist_onefile\cnc_app.exe.
    goto :error
)

echo [4/4] Copiando herramientas.json, maquinas.json, cnc_app.toml y receta_ejemplo.toml junto al exe...
for %%F in (herramientas.json maquinas.json cnc_app.toml receta_ejemplo.toml) do copy /Y "%%F" dist_onefile\ >nul

if exist build_onefile rmdir /s /q build_onefile

echo.
echo Listo: %CD%\dist_onefile\cnc_app.exe
for %%I in (dist_onefile\cnc_app.exe) do echo Fecha del exe: %%~tI - debe ser la de ahora
echo.
echo Comprobacion sin ventana: dist_onefile\cnc_app.exe --autotest ejemplo_placa.svg resultado.txt  (escribe resultado.txt)
echo.
pause
goto :eof

:error
echo.
echo Algo ha fallado; revisa los mensajes de arriba.
echo.
pause
exit /b 1

@echo off
REM Open the exported search page in Qt Designer.
REM This file stays ASCII only, same rule as the installer scripts: cmd reads a
REM BOM-less batch file using the system code page, so Chinese text in here
REM would be garbled. The Chinese explanation lives in readme.md.
REM pages\search.ui comes from dump_ui.py. Regenerate it after the real
REM interface changes, otherwise you are editing an outdated draft.
start "" pyside6-designer "%~dp0pages\search.ui"

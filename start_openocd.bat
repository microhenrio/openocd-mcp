@echo off
REM Optional manual launcher (the MCP server normally starts OpenOCD itself).
REM Uses the OpenOCD bundled in this project. Edit the -f lines for your chip.
REM Leave this window open while debugging. Press Ctrl+C to stop.

set OCD=%~dp0openocd
"%OCD%\bin\openocd.exe" -s "%OCD%\openocd\scripts" -f interface/stlink.cfg -f target/stm32g0x.cfg
pause

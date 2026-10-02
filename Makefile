
# Mandelbrot Upic
# Commodore 64 Ultimate demo -- generates a Mandelbrot fractal on-device
# at 64 MHz turbo, packed directly into Upic format, and displays it live
# via the border-color raster technique. Requires firmware 3.15 or newer
# (uses the fw 3.15+ UCI auto-enable sequence and the GET_PALETTE/
# SET_PALETTE/SET_PALETTE_COLOR/RESET_PALETTE control commands -- see
# UCILIBMANUAL.md) -- in practice an Ultimate 64 Elite 2 for now, since
# the corresponding C64U firmware hasn't been released yet.

# Target platform
SYS = c64

# Cross-platform shell detection
ifneq ($(shell echo),)
  CMD_EXE = 1
endif

ifdef CMD_EXE
  NULLDEV = nul:
  DEL     = -del /f
  RMDIR   = rmdir /s /q
  MKDIR   = mkdir
else
  NULLDEV = /dev/null
  DEL     = $(RM)
  RMDIR   = $(RM) -r
  MKDIR   = mkdir -p
endif

# Toolchain -- override the path if oscar64 lives elsewhere:
#   make CC=/path/to/oscar64/bin/oscar64
# (plain '=', not '?=' -- CC is a Make built-in with a default of 'cc',
# which is never "unset", so '?=' would silently never take effect)
CC = /home/xahmol/oscar64/bin/oscar64

# Application name
MAIN = mandelupic

# Build versioning
VERSION_MAJOR     = 1
VERSION_MINOR     = 0
VERSION_PATCH     = 3
VERSION_TIMESTAMP = $(shell date "+%Y%m%d-%H%M")
VERSION           = v$(VERSION_MAJOR).$(VERSION_MINOR).$(VERSION_PATCH)-$(VERSION_TIMESTAMP)

# Compile flags
#   -i=include       : add include/ to header search path
#   -tm=c64          : target Commodore 64
#   -tf=prg          : output standard .prg file
#   -O2              : optimise
#   -dNOFLOAT        : disable float support (saves space; also matches
#                      the fixed-point design -- see docs/MANDELBROT_ALGORITHM.md)
#   -dHEAPCHECK      : catch heap corruption in debug builds
#   -dVERSION        : pass version string to source
#   -dDATA_QUEUE_SZ / -dSTATUS_QUEUE_SZ : shrunk UCI queues -- the Upic
#                      viewer's fixed $1800-$D000 picture buffer shrinks
#                      Oscar64's own program region substantially (see
#                      include/upic_viewer.c's #pragma region(main, ...)),
#                      which the library's normal 512/256-byte queues
#                      don't fit alongside. A palette push only ever
#                      transfers 50 bytes, so 64/16 is plenty.
CFLAGS = -i=include \
         -tm=$(SYS) \
         -tf=prg \
         -O2 \
         -dNOFLOAT \
         -dHEAPCHECK \
         -dDATA_QUEUE_SZ=52 \
         -dSTATUS_QUEUE_SZ=12 \
         -dVERSION="\"$(VERSION)\""

# Main source (Oscar64 follows #pragma compile chains from here)
MAINSRC = src/main.c

# All sources that Oscar64 compiles via #pragma compile chains.
# Listed here so make rebuilds when any of them change.
ALLSRCS = $(MAINSRC) \
          include/upic_viewer.c include/upic_viewer.h \
          include/rombank.c include/rombank.h \
          include/turbo.c include/turbo.h \
          include/ultimate_common_lib.c include/ultimate_common_lib.h \
          include/mandelbrot.c include/mandelbrot.h \
          include/zoom.c include/zoom.h

# Output
TARGET = build/$(MAIN).prg

# Test-only build: always takes the 48 MHz display path (at speed index
# 14, which is 48 MHz on Elite II / C64U), so that path can be checked
# on a 64 MHz machine. Not part of the release ZIP -- see src/main.c.
FORCE48 = build/$(MAIN)-force48.prg

# Ultimate 64 config preset (enables Command Interface + U64 turbo
# registers this demo needs). Deployed/zipped as $(MAIN).cfg -- SAME
# base name as $(MAIN).prg, in the SAME directory -- so the Ultimate's
# own firmware auto-loads it whenever mandelupic.prg is run, no manual
# "load config" step needed.
CONFIGFILE = config/MandelbrotUpic-U64E2.cfg

########################################

# Demo install path on SD/USB (must match any path baked into src/main.c)
INSTALL_PATH = idi8b/mandelupic

# Ultimate device deployment target. Store only the IP in .env (gitignored,
# never committed); everything else is derived here.
-include .env
ULTIP1  ?= <set_ULTIP1_in_.env>
ULTUSB  ?= usb0
ULTPATH  = /$(ULTUSB)/$(INSTALL_PATH)/
ULTFTP1  = ftp://$(ULTIP1)$(ULTPATH)

# Optional second Ultimate device (`make deploy2`). Its storage port can
# differ from the first one's (e.g. SD on one machine, USB stick on the
# other), so it gets its own override, defaulting to the same ULTUSB.
ULTUSB2 ?= $(ULTUSB)
ifdef ULTIP2
ULTFTP2  = ftp://$(ULTIP2)/$(ULTUSB2)/$(INSTALL_PATH)/
endif

# Versioned release ZIP
ZIPFILE  = build/$(MAIN)-$(VERSION).zip
README   = README.pdf

.SUFFIXES:
.PHONY: all clean deploy check-deploy deploy2 check-deploy2 zip docs force48 test deploy-force48 e2e e2e-update

all: $(TARGET) $(README) zip

$(TARGET): $(ALLSRCS)
	@$(MKDIR) build 2>$(NULLDEV) ; true
	$(CC) $(CFLAGS) -n -o=$(TARGET) $<

force48: $(FORCE48)

$(FORCE48): $(ALLSRCS)
	@$(MKDIR) build 2>$(NULLDEV) ; true
	$(CC) $(CFLAGS) -dUPIC_FORCE_48MHZ -n -o=$(FORCE48) $<

# Host-side tests (Python 3 standard library only): run the compiled
# 6502 code from both PRGs in tests/mos6502.py's cycle-counting emulator
# against a raster-line model at 48 and 64 MHz. See tests/README.md.
test: $(TARGET) $(FORCE48)
	python3 -m unittest discover -s tests -v

# End-to-end test on real hardware (tests/e2e/README.md): runs the release
# PRG on every device in E2E_DEVICES (set in .env, host names or IPs),
# steers it with keyboard input over REST, and compares the VIC video
# stream with the golden images in tests/e2e/golden/. e2e-update rewrites
# the goldens of the devices' display paths instead.
E2E_DEVICES ?=
E2E_ARGS = $(foreach d,$(E2E_DEVICES),--device $(d))

e2e: $(TARGET)
	python3 tests/e2e/run_e2e.py $(E2E_ARGS)

e2e-update: $(TARGET)
	python3 tests/e2e/run_e2e.py --update $(E2E_ARGS)

clean:
	$(DEL) build/*.prg 2>$(NULLDEV) ; true
	$(DEL) build/*.map 2>$(NULLDEV) ; true
	$(DEL) build/*.asm 2>$(NULLDEV) ; true
	$(DEL) build/*.lbl 2>$(NULLDEV) ; true
	$(DEL) build/*.zip 2>$(NULLDEV) ; true

# Regenerate README.pdf from README.md (requires pandoc + texlive-xetex).
# Install: sudo apt install pandoc texlive-xetex
# Warns and skips (does not fail the build) if pandoc is unavailable, since
# README.pdf is committed to git and only needs regenerating when docs change.
docs: $(README)

$(README): README.md pandoc-defaults.yaml pandoc-header.tex
	@if which pandoc >/dev/null 2>&1; then \
		pandoc --defaults=pandoc-defaults.yaml README.md -o $(README); \
	else \
		echo "WARNING: pandoc not found -- $(README) not updated (install: sudo apt install pandoc texlive-xetex)"; \
	fi

zip: $(TARGET)
	$(MKDIR) build/$(INSTALL_PATH) 2>$(NULLDEV) ; true
	cp $(TARGET) build/$(INSTALL_PATH)/$(MAIN).prg
	cp $(CONFIGFILE) build/$(INSTALL_PATH)/$(MAIN).cfg
	cp README.md build/$(INSTALL_PATH)/README.md
	cd build && zip -r $(MAIN)-$(VERSION).zip idi8b/
	$(RMDIR) build/idi8b 2>$(NULLDEV) ; true

# Safety check before deploy: make sure the Ultimate device is actually reachable
check-deploy:
	@curl -s --connect-timeout 3 ftp://$(ULTIP1)/ >/dev/null 2>&1 || \
		(echo "ERROR: Cannot reach Ultimate device at $(ULTIP1) -- check ULTIP1 in .env" && false)

deploy: check-deploy $(TARGET)
	wput -u $(TARGET) $(ULTFTP1)$(MAIN).prg
	wput -u $(CONFIGFILE) $(ULTFTP1)$(MAIN).cfg

# Deploys the force48 test PRG next to the release one, with its own
# copy of the config so the firmware auto-loads it the same way.
deploy-force48: check-deploy $(FORCE48)
	wput -u $(FORCE48) $(ULTFTP1)$(MAIN)-force48.prg
	wput -u $(CONFIGFILE) $(ULTFTP1)$(MAIN)-force48.cfg

check-deploy2:
ifndef ULTIP2
	$(error ULTIP2 is not set -- add it to .env to use deploy2)
endif
	@curl -s --connect-timeout 3 ftp://$(ULTIP2)/ >/dev/null 2>&1 || \
		(echo "ERROR: Cannot reach Ultimate device at $(ULTIP2) -- check ULTIP2 in .env" && false)

deploy2: check-deploy2 $(TARGET)
	wput -u $(TARGET) $(ULTFTP2)$(MAIN).prg
	wput -u $(CONFIGFILE) $(ULTFTP2)$(MAIN).cfg

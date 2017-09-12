#!/usr/bin/env python
# (c) Stefan Countryman, 2017
# Write a timing witness paper template

import sys
import os
import glob
import gwpy.time
import shutil
import subprocess

SCRIPTDIR = os.path.dirname(os.path.realpath(__file__))
TEMPLATEDIRNAME = 'timing_checks.d'
TEMPLATEPATH = os.path.join(SCRIPTDIR, TEMPLATEDIRNAME, 'main.tex')
AUXDIR = os.path.join(SCRIPTDIR, TEMPLATEDIRNAME, 'auxiliary_files')
USAGE = """USAGE:

  """ + sys.argv[0] + """ GRACEID GPSTIME OUTDIR

  Outputs a timing witness TEX file into the specified output
  directory. The output directory must have the required plots,
  which are generated by the timing_checks.py script. The timing
  witness paper is generated for the given GPS time and GraceID.
"""

if (len(sys.argv) == 0) or ("-h" in sys.argv):
    print(USAGE)
    if len(sys.argv) == 0:
        exit(1)
    else:
        exit(0)
else:
    graceid = sys.argv[1]
    gpstime = sys.argv[2]
    outdir  = os.path.abspath(sys.argv[3])
    utctime = gwpy.time.tconvert(int(gpstime)).strftime("%c UTC")
    outfilepath = os.path.join(outdir, "main.tex")

# define some straightforward text substitutions for the template TEX file
SUBSTITUTIONS = {
    "$GRACEID": graceid,
    "$GPSTIME": str(gpstime),
    "$UTCTIME": utctime
}

# these are the file name globs for the plots made for each event. these name
# substitutions depend on the files in the directory. this is my lazy solution
# that avoids having to figure out the actual filenames since the plotting
# libraries I wrote are a mess and don't have easily callable functions for
# these.
GLOB_SUBSTITUTIONS = {
    "$LHO_X_DT": "H1..CAL-PCALX_FPGA_DTONE_IN1_DQ-Overlay-*.png",
    "$LHO_Y_DT": "H1..CAL-PCALY_FPGA_DTONE_IN1_DQ-Overlay-*.png",
    "$LLO_Y_DT": "L1..CAL-PCALY_FPGA_DTONE_IN1_DQ-Overlay-*.png",
    "$LLO_X_DT": "L1..CAL-PCALX_FPGA_DTONE_IN1_DQ-Overlay-*.png",
    "$HX_DT_FULL": "H1..CAL-PCALX_FPGA_DTONE_IN1_DQ-DuoTone-Statistics-full-*.png",
    "$HY_DT_FULL": "H1..CAL-PCALY_FPGA_DTONE_IN1_DQ-DuoTone-Statistics-full-*.png",
    "$LY_DT_FULL": "L1..CAL-PCALY_FPGA_DTONE_IN1_DQ-DuoTone-Statistics-full-*.png",
    "$LX_DT_FULL": "L1..CAL-PCALX_FPGA_DTONE_IN1_DQ-DuoTone-Statistics-full-*.png",
    "$HX_DT_ZOOM": "H1..CAL-PCALX_FPGA_DTONE_IN1_DQ-DuoTone-Statistics-zero-crossing-zoom-*.png",
    "$HY_DT_ZOOM": "H1..CAL-PCALY_FPGA_DTONE_IN1_DQ-DuoTone-Statistics-zero-crossing-zoom-*.png",
    "$LY_DT_ZOOM": "L1..CAL-PCALY_FPGA_DTONE_IN1_DQ-DuoTone-Statistics-zero-crossing-zoom-*.png",
    "$LX_DT_ZOOM": "L1..CAL-PCALX_FPGA_DTONE_IN1_DQ-DuoTone-Statistics-zero-crossing-zoom-*.png",
    "$HX_DT_SUPER_ZOOM": "H1..CAL-PCALX_FPGA_DTONE_IN1_DQ-DuoTone-Statistics-super-zoom-*.png",
    "$HY_DT_SUPER_ZOOM": "H1..CAL-PCALY_FPGA_DTONE_IN1_DQ-DuoTone-Statistics-super-zoom-*.png",
    "$LY_DT_SUPER_ZOOM": "L1..CAL-PCALY_FPGA_DTONE_IN1_DQ-DuoTone-Statistics-super-zoom-*.png",
    "$LX_DT_SUPER_ZOOM": "L1..CAL-PCALX_FPGA_DTONE_IN1_DQ-DuoTone-Statistics-super-zoom-*.png",
    "$HX_IRIGB_FULL": "H1..CAL-PCALX_IRIGB_OUT_DQ-Overlay-*.png",
    "$HY_IRIGB_FULL": "H1..CAL-PCALY_IRIGB_OUT_DQ-Overlay-*.png",
    "$LY_IRIGB_FULL": "L1..CAL-PCALY_IRIGB_OUT_DQ-Overlay-*.png",
    "$LX_IRIGB_FULL": "L1..CAL-PCALX_IRIGB_OUT_DQ-Overlay-*.png"
}

# copy auxiliary files to the output directory
for filename in os.listdir(AUXDIR):
    if not os.path.isfile(os.path.join(outdir, filename)):
        shutil.copy(os.path.join(AUXDIR, filename), outdir)

# put ourselves in the output directory
os.chdir(outdir)

# read paper template and substitute out placeholder variables, then write
# everything to the final output file
with open(TEMPLATEPATH) as infile:
    with open(outfilepath, 'w') as outfile:
        for line in infile:
            for string in SUBSTITUTIONS.keys():
                line = line.replace(string, SUBSTITUTIONS[string])
            for string in GLOB_SUBSTITUTIONS.keys():
                # this will fail if a needed file is missing
                path = glob.glob(GLOB_SUBSTITUTIONS[string])[0]
                line = line.replace(string, path)
            outfile.write(line)

# compile the PDF
command = ['pdflatex', outfilepath]
proc = subprocess.Popen(command)
res, err = proc.communicate()
if proc.returncode != 0:
    raise Exception('Something went wrong while generating the PDF file.')

# make a copy of the file with the appropriate filename for DCC upload; will
# overwrite the old file. this file version lacks a DCC number, since we
# aren't making those automatically.
dcc_name = 'aLIGO_BBH_Candidate_TimingWitness_{}_NODCC.pdf'.format(graceid)
shutil.copy(os.path.join(outdir, 'main.pdf'),
            os.path.join(outdir, dcc_name))

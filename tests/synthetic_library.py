# SPDX-License-Identifier: GPL-3.0-or-later
"""A synthetic Poser content root for the library-index tests (test_library_index.py, test_library_build.py)."""

import pytest


def _actor(channels, name="head:1"):
    return f"\tactor {name}\n\t\t{{\n\t\tchannels\n\t\t\t{{\n{channels}\t\t\t}}\n\t\t}}\n"


def _file(*actors):
    return "{\n\tversion\n\t\t{\n\t\tnumber 4.01\n\t\t}\n" + "".join(actors) + "}\n"


INJ_LEAF = _file(_actor(
    "\t\t\ttargetGeom Foo\n\t\t\t\t{\n\t\t\t\tname Foo\n\t\t\t\tindexes 2\n\t\t\t\tnumbDeltas 500\n"
    "\t\t\t\tdeltas\n\t\t\t\t\t{\n\t\t\t\t\td 0 0 0 1\n\t\t\t\t\td 1 0 0 1\n\t\t\t\t\t}\n\t\t\t\t}\n"))
REM_LEAF = _file(_actor(
    "\t\t\ttargetGeom Foo\n\t\t\t\t{\n\t\t\t\tname -\n\t\t\t\tindexes 0\n\t\t\t\tnumbDeltas 0\n\t\t\t\t}\n"))
# The trap: `name -` on a valueParm, and a bare hidden targetGeom (Victoria 3 ChanVis files).
HIDE_LEAF = _file(
    _actor("\t\t\tvalueParm Foo\n\t\t\t\t{\n\t\t\t\tname -\n\t\t\t\thidden 1\n\t\t\t\t}\n", name="BODY:1"),
    _actor("\t\t\ttargetGeom Foo\n\t\t\t\t{\n\t\t\t\thidden 1\n\t\t\t\t}\n", name="abdomen:1"))
POSE_LEAF = _file(_actor(
    "\t\t\ttargetGeom Foo\n\t\t\t\t{\n\t\t\t\tkeys\n\t\t\t\t\t{\n\t\t\t\t\tk 0 0.5\n\t\t\t\t\t}\n\t\t\t\t}\n"))
PMD_LEAF = _file(_actor(
    "\t\t\ttargetGeom Foo\n\t\t\t\t{\n\t\t\t\tname Foo\n\t\t\t\tuseBinaryMorph 1\n\t\t\t\tindexes 9\n\t\t\t\t}\n"))
OTHER_LEAF = _file(_actor("\t\t\trotateX xRotate\n\t\t\t\t{\n\t\t\t\tkeys\n\t\t\t\t\t{\n\t\t\t\t\tk 0 0\n\t\t\t\t\t}\n\t\t\t\t}\n"))


def _orchestrator(*targets):
    return "{\n" + "".join(f'\treadScript "{t}"\n' for t in targets) + "}\n"


@pytest.fixture
def lib(tmp_path):
    """A content root with a Runtime/libraries tree; write() takes paths below libraries/."""
    root = tmp_path / "Content"

    class Lib:
        path = str(root)

        def write(self, rel, text, binary=False):
            target = root / "Runtime" / "libraries" / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            if binary:
                target.write_bytes(text)
            else:
                target.write_text(text)
            return str(target)

    return Lib()




@pytest.fixture
def library(lib):
    lib.write("!DAZ/Fig/Deltas/InjDeltas.Brow.pz2", INJ_LEAF)
    lib.write("!DAZ/Fig/Deltas/RemDeltas.Brow.pz2", REM_LEAF)
    lib.write("!DAZ/Fig/Deltas/Hide.Brow.pz2", HIDE_LEAF)
    lib.write("pose/Fig INJ/Brow.pz2", _orchestrator(":Runtime:libraries:!DAZ:Fig:Deltas:InjDeltas.Brow.pz2"))
    lib.write("pose/Fig INJ/Brow.png", "png")
    lib.write("pose/Fig REM/Brow.pz2", _orchestrator(":Runtime:libraries:!DAZ:Fig:Deltas:RemDeltas.Brow.pz2"))
    lib.write("pose/Fig REM/Brow.rsr", "rsr")
    lib.write("pose/Fig2 INJ/Brow.pz2", _orchestrator(":Runtime:libraries:!DAZ:Fig:Deltas:InjDeltas.Brow.pz2"))
    lib.write("pose/Fig2 INJ/Half.pz2", _orchestrator(
        ":Runtime:libraries:!DAZ:Fig:Deltas:InjDeltas.Brow.pz2", ":Runtime:libraries:gone.pz2"))
    lib.write("pose/Poses/Wave.pz2", POSE_LEAF)
    lib.write("pose/Poses/Hide Belly.pz2", _orchestrator(":Runtime:libraries:!DAZ:Fig:Deltas:Hide.Brow.pz2"))
    lib.write("pose/Props/Plain.pz2", OTHER_LEAF)
    return lib

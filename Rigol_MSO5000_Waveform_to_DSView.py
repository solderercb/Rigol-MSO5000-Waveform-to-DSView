#!/usr/bin/env python3

import json
import math
import os
import struct
import sys
import time
from pathlib import Path
import shutil
import tempfile
import zipfile
import logging

# logging.basicConfig(level=logging.DEBUG)
# logging.basicConfig(level=logging.WARNING)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger()

BLOCK_FILE_SIZE = 2 * 1024 * 1024
ADDITIONAL_INFO_SIZE = 100


class DataBlock:
    def __init__(self):
        self.type = 0
        self.samples_count = 0
        self.sampling_time = 0.0
        self.trigger_offset = 0.0
        self.sampling_period = 0.0
        self.trigger_offset2 = 0.0
        self.array_size = 0
        self.samples = None


def read_input_file(filename):
    blocks = []

    with open(filename, "rb") as f:

        magic = f.read(4)
        if magic != b'RG01':
            print("Unsupported file format")
            sys.exit(1)

        full_size, total_blocks = struct.unpack("<II", f.read(8))
        real_size = os.path.getsize(filename)

        if full_size > real_size:
            logger.info("Buggy dump detected")
            logger.info("    Header corrected")

        for n in range(total_blocks):

            (
                unkn00,
                block_type,
                unkn08,
                samples_count,
                unkn10,
            ) = struct.unpack("<IIIII", f.read(20))

            sampling_time = struct.unpack("<f", f.read(4))[0]

            trigger_offset = struct.unpack("<d", f.read(8))[0]
            sampling_period = struct.unpack("<d", f.read(8))[0]
            trigger_offset2 = struct.unpack("<d", f.read(8))[0]

            f.read(ADDITIONAL_INFO_SIZE)

            array_size = struct.unpack("<I", f.read(4))[0]

            block = DataBlock()

            block.type = block_type
            block.samples_count = samples_count
            block.sampling_time = sampling_time
            block.trigger_offset = trigger_offset
            block.sampling_period = sampling_period
            block.trigger_offset2 = trigger_offset2
            block.array_size = array_size

            # logger.debug(f"type = {block_type}")
            if (full_size > real_size) and (block_type == 6):
                fix_corrupted_block(block)
                logger.info(f"    Header in block {n} corrected");

            sample_count = block.array_size // 4

            logger.debug(f"samples = {block.samples_count}")
            logger.debug(f"array_size = {block.array_size}")
            logger.debug(f"file_pos = {f.tell()}")

            raw = f.read(block.array_size)

            block.samples = struct.unpack(
                f"<{block.samples_count}f",
                raw
            )

            blocks.append(block)

        if full_size > real_size:
            full_size = real_size - (total_blocks - 1) * 152

    return (
        magic,
        full_size,
        total_blocks,
        blocks
    )


def write_file_chunks(directory, data):
    directory.mkdir(parents=True, exist_ok=True)

    offset = 0
    index = 0

    while offset < len(data):
        chunk = data[offset:offset + BLOCK_FILE_SIZE]

        with open(directory / str(index), "wb") as f:
            f.write(chunk)

        offset += BLOCK_FILE_SIZE
        index += 1


def format_with_si_prefix(number):
    prefixes = [
        (1e12, "T"),
        (1e9, "G"),
        (1e6, "M"),
        (1e3, "k"),
        (1, ""),
        (1e-3, "m"),
        (1e-6, "u"),
        (1e-9, "n"),
        (1e-12, "p")
    ]

    if number == 0:
        return "0"

    for value, prefix in prefixes:
        if abs(number) >= value:
            scaled = number / value

            if scaled % 1 == 0:
                return f"{int(scaled)} {prefix}"
            else:
                return f"{scaled:.2f} {prefix}".rstrip('0')

    # Для очень маленьких чисел — используем наименьший префикс
    return f"{number:.2e} "

def create_logic_project(project_dir, logic_blocks):

    samples_count = logic_blocks[0].samples_count
    sampling_period = logic_blocks[0].sampling_period

    samplerate = round(1.0 / sampling_period / 1000) * 1000
    logger.debug(f"samplerate: {samplerate}")

    trigger_pos = round(
        logic_blocks[0].trigger_offset /
        sampling_period
    )

    channel_count = len(logic_blocks) * 8

    total_blocks = math.ceil(
        samples_count / (BLOCK_FILE_SIZE * 8)
    )

    project_dir.mkdir(parents=True, exist_ok=True)

    header = []
    header.append("[version]")
    header.append("version = 3")
    header.append("[header]")
    header.append("driver = RIGOL")
    header.append("device mode = 0")
    header.append("capturefile = data")
    header.append(f"total samples = {samples_count}")
    header.append(f"total probes = {channel_count}")
    header.append(f"total blocks = {total_blocks}")
    header.append(f"samplerate = {format_with_si_prefix(samplerate)}Hz")
    header.append(
        f"trigger time = {int(time.time()*1000)}"
    )
    header.append(f"trigger pos = {trigger_pos}")

    for ch in range(channel_count):
        header.append(f"probe{ch} = {ch}")

    (project_dir / "header").write_text(
        "\n".join(header),
        encoding="utf-8"
    )

    channels = []

    for block in logic_blocks:

        for bit in range(8):

            channel = bytearray(
                (samples_count + 7) // 8
            )

            for sample_idx in range(samples_count):

                state = int(round(block.samples[sample_idx])) & 0xFF

                value = (state >> bit) & 1

                if value:
                    byte_index = sample_idx >> 3
                    bit_index = sample_idx & 7

                    channel[byte_index] |= (
                        1 << bit_index
                    )

            channels.append(channel)

    for ch, data in enumerate(channels):
        write_file_chunks(
            project_dir / f"L-{ch}",
            data
        )

    session = {
        "Channel Mode": 21,
        "CollectMode": 0,
        "Device": "RIGOL",
        "DeviceMode": 0,
        "Enable RLE Compress": 0,
        "Filter Targets": 0,
        "Horizontal trigger position": 0,
        "Language": 31,
        "Max Height": "3X",
        "Operation Mode": 0,
        "Sample count": str(samples_count),
        "Sample rate": str(samplerate),
        "Stop Options": 1,
        "Threshold Level": "1",
        "Title": "DSView v1.3.2",
        "Trigger channel": 0,
        "Trigger hold off": "0",
        "Trigger margin": 8,
        "Trigger slope": 0,
        "Trigger source": 0,
        "Using Clock Negedge": 0,
        "Using External Clock": 0,
        "Version": 3,
        "channel": [],
        "decoder": [],
        "trigger": {
            "triggerPos": 1,
            "triggerStages": 3,
            "triggerTab": 0,
            "advTriggerMode": False
        }
    }

    for ch in range(channel_count):
        session["channel"].append({
            "colour": "default",
            "enabled": True,
            "index": ch,
            "name": str(ch),
            "strigger": 0,
            "type": 10000,
            "view_index": ch + 1
        })

    with open(project_dir / "session", "w") as f:
        json.dump(session, f, indent=4)


def create_analog_project(project_dir, analog_blocks):

    samples_count = analog_blocks[0].samples_count
    sampling_period = analog_blocks[0].sampling_period

    samplerate = round(1.0 / sampling_period / 1000)*1000
    logger.debug(f"samplerate: {samplerate}")

    trigger_pos = round(
        analog_blocks[0].trigger_offset /
        sampling_period
    )

    channel_count = len(analog_blocks)

    tmp = max(
        max(block.samples)
        for block in analog_blocks
    )
    global_max = ((tmp // 5) + 1) * 5
    logger.debug(f"max: {global_max}")

    tmp = min(
        min(block.samples)
        for block in analog_blocks
    )
    # global_min = math.floor( tmp / 5) * 5
    global_min = -global_max
    logger.debug(f"min: {global_min}")

    scale = (global_max - global_min)/256

    # tmp = sum(
    #     sum(block.samples) / len(block.samples)
    #     for block in analog_blocks) / len(analog_blocks)
    # logger.debug(f"avg: {tmp}")

    vOffset = 128
    logger.debug(f"vOffset: {vOffset}")

    project_dir.mkdir(parents=True, exist_ok=True)

    total_samples_in_data = (
        samples_count * channel_count
    )

    total_blocks = math.ceil(
        total_samples_in_data /
        BLOCK_FILE_SIZE
    )

    header = []
    header.append("[version]")
    header.append("version = 3")
    header.append("[header]")
    header.append("driver = RIGOL")
    header.append("device mode = 2")
    header.append("capturefile = data")
    header.append(f"total samples = {samples_count}")
    header.append(f"total probes = {channel_count}")
    header.append(f"total blocks = {total_blocks}")
    header.append(f"samplerate = {format_with_si_prefix(samplerate)}Hz")
    header.append("bits = 8")
    header.append(f"trigger pos = {trigger_pos}")

    for ch in range(channel_count):
        header.append(f"probe{ch} = {ch}")
        header.append(f" enable{ch} = 1")
        header.append(f" coupling{ch} = 0")
        header.append(f" vDiv{ch} = 1000")
        header.append(f" vOffset{ch} = {vOffset}")
        header.append(f" mapUnit{ch} = V")
        header.append(f" mapMax{ch} = {global_max}")
        header.append(f" mapMin{ch} = {global_min}")

    (project_dir / "header").write_text(
        "\n".join(header),
        encoding="utf-8"
    )

    data = bytearray()

    for sample_idx in range(samples_count):

        for block in analog_blocks:

            value = block.samples[sample_idx]

            code = vOffset - round(value / scale)
            # logger.debug(f"code: {round(value / scale)}")

            code = max(0, min(255, code))

            data.append(code)

    # data[0] = vOffset - round(3.3 / (scale/2))
    logger.debug(f"data[0]: {data[0]}")
    logger.debug(f"scale: {scale}")

    write_file_chunks(
        project_dir / "A-0",
        data
    )

    session = {
        "Device": "RIGOL",
        "DeviceMode": 2,
        "Language": 25,
        "Max Height": "1X",
        "Sample count": str(samples_count),
        "Sample rate": str(samplerate),
        "Title": "DSView v1.3.0",
        "Version": 3,
        "channel": [],
        "decoder": []
    }

    for ch in range(channel_count):

        session["channel"].append({
            "colour": "#eeb211",
            "coupling": 0,
            "enabled": True,
            "index": ch,
            "mapDefault": True,
            "mapMax": global_max,
            "mapMin": global_min,
            "mapUnit": "V",
            "name": str(ch),
            "type": 10002,
            "vdiv": 1000,
            "vfactor": 1,
            "zeroPos": 0.5
        })

    with open(project_dir / "session", "w") as f:
        json.dump(session, f, indent=4)

def fix_corrupted_block(block):
    old_samples = block.samples_count
    old_array = block.array_size

    block.samples_count //= 8
    block.array_size //= 8
    block.sampling_time = (
        block.samples_count *
        block.sampling_period
    )

def make_dsl_archive(project_dir, archive_name):

    with zipfile.ZipFile(
        archive_name,
        "w",
        zipfile.ZIP_DEFLATED
    ) as z:

        for root, _, files in os.walk(project_dir):

            for file in files:

                full_path = Path(root) / file

                rel_path = full_path.relative_to(
                    project_dir
                )

                z.write(
                    full_path,
                    rel_path.as_posix()
                )

def main():
    if len(sys.argv) != 2:
        print("usage: converter.py input.bin")
        sys.exit(1)

    source_file = Path(sys.argv[1])

    allowed = ['.bin']
    ext = Path(source_file).suffix
    if ext.lower() not in allowed:
        print("Only binary (.bin) waveforms supported")
        sys.exit(1)

    (
        magic,
        full_size,
        total_blocks,
        blocks
    ) = read_input_file(source_file)

    analog_blocks = [
        b for b in blocks
        if b.type == 1
    ]

    logic_blocks = [
        b for b in blocks
        if b.type == 6
    ]

    temp_root = (
        Path(
            os.environ["TEMP"]
        ) / "Rigol-conv"
    )

    temp_root.mkdir(
        parents=True,
        exist_ok=True
    )

    base_name = source_file.stem

    analog_project = None
    logic_project = None

    if analog_blocks:

        analog_project = (
            temp_root /
            f"{base_name}_analog"
        )

        create_analog_project(
            analog_project,
            analog_blocks
        )

    if logic_blocks:

        logic_project = (
            temp_root /
            f"{base_name}_logic"
        )

        create_logic_project(
            logic_project,
            logic_blocks
        )

    if analog_project:

        archive_name = (
            source_file.parent /
            f"{base_name}_analog.dsl"
        )

        make_dsl_archive(
            analog_project,
            archive_name
        )

        logger.info(f"created: {archive_name}")

    if logic_project:

        archive_name = (
            source_file.parent /
            f"{base_name}_logic.dsl"
        )

        make_dsl_archive(
            logic_project,
            archive_name
        )

        logger.info(f"created: {archive_name}")

    shutil.rmtree(temp_root)


if __name__ == "__main__":
    main()
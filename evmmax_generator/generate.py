import math
import os
import random
import sys
import subprocess

import numpy as np

EVMMAX_ARITH_ITER_COUNT = 1

MAX_LIMBS = 12

EVMMAX_ARITH_OPS = {
    "ADDMODX": "c3",
    "SUBMODX": "c4",
    "MULMONTX": "c5",
}

LIMB_SIZE = 8

OP_SETMOD = "c0"
OP_STOREX = "c2"

EVM_OPS = {
    "POP": "50",
    "MSTORE": "52",
}

def reverse_endianess(word: str):
    assert len(word) == LIMB_SIZE * 2, "invalid length"

    result = ""
    for i in reversed(range(0, len(word), 2)):
        result += word[i:i+2]
    return result

def calc_limb_count(val: int) -> int:
    assert val > 0, "val must be greater than 0"

    count = 0
    while val != 0:
        val >>= 64
        count += 1
    return count

# split a value into 256bit big-endian words, return them in big-endian format
def int_to_evm_words(val: int, res_size: int) -> [str]:
    result = []
    if val == 0:
        return ['00']

    og_val = val
    while val != 0:
        limb = val % (1 << 256)
        val >>= 256

        if limb == 0:
            result.append("00")
            continue

        limb_hex = hex(limb)[2:]
        if len(limb_hex) % 2 != 0:
            limb_hex = "0" + limb_hex

        if len(limb_hex) < 64:
            limb_hex += (64 - len(limb_hex)) * "0"

        result.append(limb_hex)

    return result

def gen_push_int(val: int) -> str:
    assert val >= 0 and val < (1 << 256), "val must be in acceptable evm word range"

    literal = hex(val)[2:]
    if len(literal) % 2 == 1:
        literal = "0" + literal
    return gen_push_literal(literal)

def gen_push_literal(val: str) -> str:
    assert len(val) <= 64, "val is too big"
    assert len(val) % 2 == 0, "val must be even length"
    push_start = 0x60
    push_op = hex(push_start - 1 + int(len(val) / 2))[2:]

    assert len(push_op) == 2, "bug"

    return push_op + val

def gen_mstore_int(val: int, offset: int) -> str:
    return gen_push_int(val) + gen_push_int(offset) + EVM_OPS["MSTORE"]

def gen_mstore_literal(val: str, offset: int) -> str:
    return gen_push_literal(val) + gen_push_int(offset) + EVM_OPS["MSTORE"]

def reverse_endianess(val: str):
    assert len(val) % 2 == 0, "must have even string"
    result = ""
    for i in reversed(range(0, len(val), 2)):
        result += val[i:i+2]

    return result

def gen_random_val(modulus: int) -> int:
    return random.randrange(0, modulus)

def calc_field_elem_size(mod: int) -> int:
    mod_byte_count = int(math.ceil(len(hex(mod)[2:]) / 2))
    mod_u64_count = (mod_byte_count - 7) // 8
    return mod_u64_count * 8

def gen_random_scratch_space(dst_mem_offset: int, mod: int, scratch_count: int) -> str:
    field_elem_size = calc_field_elem_size(mod)
    # allocate the scratch space: store 0 at the last byte
    res = gen_mstore_int(0, dst_mem_offset + field_elem_size * scratch_count)

    for i in range(scratch_count):
        res += gen_mstore_field_elem(dst_mem_offset + i * field_elem_size, gen_random_val(mod), field_elem_size // 8)

    return res


# store a 64bit aligned field element (size limb_count * 8 bytes) in big-endian
# repr to EVM memory
def gen_mstore_field_elem(dst_offset: int, val: int, field_width_bytes: int) -> str:
    evm_words = int_to_evm_words(val, field_width_bytes)
    result = ""
    offset = dst_offset
    for word in evm_words:
        result += gen_mstore_literal(word, offset)
        offset += 32

    return result

# store a value at a memory offset in big-endian
def gen_mstore_bigint(dst_offset: int, val: int) -> str:
    evm_words = int_to_evm_words(val, limb_count)
    result = ""
    for word in evm_words:
        result += gen_mstore_literal(word, dst_offset)
        dst_offset += 32

    return result

def gen_storex(dst_slot: int, count: int, src_offset: int) -> str:
    return gen_push_int(src_offset) + gen_push_int(count) + gen_push_int(dst_slot) + OP_STOREX


def size_bytes(val: int) -> int:
    val_hex = hex(val)[2:]
    if len(val_hex) % 2 != 0:
        val_hex = '0'+val_hex
    return len(val_hex) // 2

def gen_encode_arith_immediate(out, x, y) -> str:
    result = ""
    for b1 in [out, x, y]:
        assert b1 >= 0 and b1 < 256, "argument must be in byte range"

        b1 = hex(b1)[2:]
        if len(b1) == 1:
            b1 = '0'+b1

        result += b1
    return result

def encode_single_byte(val: int) -> str:
    assert val < 256, "val mus tbe representable as a byte"
    result = hex(val)[2:]
    if len(result) == 1:
        result = '0' + result
    return result

def gen_setmod(mod: int) -> str:
    mod_size = size_bytes(mod)
    field_elem_size = calc_field_elem_size(mod)

    result = gen_mstore_field_elem(0, mod, field_elem_size) # store big-endian modulus to memory at offset 0
    result += gen_push_literal(encode_single_byte(0)) # source offset
    result += gen_push_int(mod_size) # mod size
    result += gen_push_int(0) # mod-id, not used
    result += OP_SETMOD 
    return result

def gen_arith_op(op: str, out_slot: int, x_slot: int, y_slot: int) -> str:
    return EVMMAX_ARITH_OPS[op] + gen_encode_arith_immediate(out_slot, x_slot, y_slot)

MAX_CONTRACT_SIZE = 24576


def gen_benchmark(op: str, mod: int):
    bench_code = ""

    # setmod
    bench_code += gen_setmod(mod)

    # store inputs
    bench_code += gen_random_scratch_space(0, mod, 256)

    # storex
    bench_code += gen_storex(0, 256, 0)

    # loop

    arr = np.array([i for i in range(256)])
    p1 = np.random.permutation(arr)
    p2 = np.random.permutation(arr)
    p3 = np.random.permutation(arr)

    scratch_space_vals = [(p1[i], p2[i], p3[i]) for i in range(len(arr))]
    # TODO: generate the calls
    iter_count = 5000

    inner_loop_arith_op_count = 0
    loop_body = ""

    for i in range(iter_count):
        loop_body += gen_arith_op(op, p1[i % len(arr)], p2[i % len(arr)], p3[i % len(arr)])
        inner_loop_arith_op_count += 1

    bench_code = gen_loop().format(bench_code, loop_body, gen_push_int(int(len(bench_code) / 2) + 33))
    return bench_code, inner_loop_arith_op_count 
# bench some evm bytecode and return the runtime in ns

def gen_loop() -> str:
    return "{}7fffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff015b{}60010180{}57"

def bench_geth(code: str) -> int:
    geth_path = "go-ethereum/build/bin/evm"
    if os.getenv('GETH_EVM') != None:
        geth_path = os.getenv('GETH_EVM')

    geth_exec = os.path.join(os.getcwd(), geth_path)
    geth_cmd = "{} --code {} --bench run".format(geth_exec, code)
    result = subprocess.run(geth_cmd.split(' '), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode != 0:
        raise Exception("geth exec error: {}".format(result.stderr))

    exec_time = str(result.stderr).split('\\n')[1].strip('execution time:  ')

    if exec_time.endswith("ms"):
        exec_time = int(float(exec_time[:-2]) * 1000000)
    elif exec_time.endswith("s"):
        import pdb; pdb.set_trace()
        exec_time = int(float(exec_time[:-1]) * 1000000 * 1000)
    else:
        raise Exception("unknown timestamp ending: {}".format(exec_time))

    return exec_time

LOOP_ITERATIONS = 255

# return a value between [1<<min_size_bits, 1<<max_size_bits)
def gen_random_mod(min_size_bits: int, max_size_bits: int):
    return random.randrange(1 << min_size_bits, 1 << max_size_bits)

def generate_and_run_benchmark(arith_op_name: str, mod: int) -> (int, int):
    bench_code, evmmax_op_count = gen_benchmark(arith_op_name, mod)

    return bench_geth(bench_code), evmmax_op_count

def bench_run(benches):
    for op_name, limb_count_min, limb_count_max in benches:
        for i in range(limb_count_min, limb_count_max + 1):
            evmmax_bench_time, evmmax_op_count = bench_geth_evmmax(op_name, i) 

            setmod_est_time = 0 # TODO

            est_time = math.ceil((evmmax_bench_time) / (evmmax_op_count * LOOP_ITERATIONS))
            #print("{} - {} limbs - {} ns/op".format(arith_op_name, limb_count, est_time))
            print("{},{},{}".format(op_name, limb_count, est_time))

def bench_all():
    print("op name, input size (in 8-byte increments), opcode runtime est (ns)")
    for arith_op_name in ["ADDMODX", "SUBMODX", "MULMONTX"]:
        for limb_count in range(1, 17):
            mod = gen_random_mod(limb_count * 8 * 8, (limb_count + 1) * 8 * 8)
            bench_code, arith_op_count = gen_benchmark(arith_op_name, mod)

            for i in range(5):
                evmmax_bench_time, evmmax_op_count = bench_geth(arith_op_name) 
                bench_time = bench_geth(bench_code)

                print("bench time is {}".format(bench_time))

if __name__ == "__main__":
    if len(sys.argv) == 1:
        bench_all()
    elif len(sys.argv) >= 2:
        op = sys.argv[1]
        if op != "ADDMODX" and op != "SUBMODX" and op != "MULMONTX":
            print(op)
            raise Exception("unknown op")

        limb_count = int(sys.argv[2])
        if limb_count < 0 or limb_count > 12:
            raise Exception("must choose limb count between 1 and 12")

        if len(sys.argv) == 4:
            if sys.argv[3] == "dumpgethcmd":
                bench_code, evmmax_op_count = gen_arith_loop_benchmark(op, limb_count)
                print(bench_code)
        else:
            bench_code, evmmax_op_count = gen_arith_loop_benchmark(op, limb_count)
            bench_run([(op, limb_count, limb_count)])

    else:
        print("too many args")


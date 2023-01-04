import math
import os
import sys
import subprocess

EVMMAX_ARITH_ITER_COUNT = 1

MAX_LIMBS = 16

EVMMAX_ARITH_OPS = {
    "ADDMODX": "22",
    "SUBMODX": "23",
    "MULMONTX": "24",
}

LIMB_SIZE = 8

SETMOD_OP = "21"

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

# split a value into 256bit big-endian words, return them in little-endian format
def int_to_evm_words(val: int, evm384_limb_count: int) -> [str]:
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

        #limb_hex = reverse_endianess(limb_hex)
        if len(limb_hex) < 64:
            limb_hex += (64 - len(limb_hex)) * "0"

        result.append(limb_hex)

    # if len(result) * 32 < evm384_limb_count * LIMB_SIZE:
    #    result = ['00'] * math.ceil((evm384_limb_count * LIMB_SIZE - len(result) * 32) / 32) + result

    return list(reversed(result))

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

def gen_mstore_evmmax_elem(dst_slot: int, val: int, limb_count: int) -> str:
    assert dst_slot >= 0 and dst_slot < 11, "invalid dst_slot"

    evm_words = int_to_evm_words(val, limb_count)
    result = ""
    offset = dst_slot * limb_count * LIMB_SIZE
    for word in evm_words:
        result += gen_mstore_literal(word, offset)
        offset += 32

    return result

def gen_encode_evmmax_bytes(*args):
    result = ""
    for b1 in args:
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

def gen_setmod(slot: int, mod: int) -> str:
    limb_count = calc_limb_count(mod)
    result = gen_mstore_evmmax_elem(slot, mod, limb_count)
    result += gen_push_literal(encode_single_byte(0))
    result += gen_push_literal(encode_single_byte(limb_count))
    result += gen_push_literal(encode_single_byte(slot))
    result += SETMOD_OP
    return result

# return modulus roughly in the middle of the range that can be represented with limb_count
#def gen_mod(limb_count: int) -> int:
#    mod = (1 << ((limb_count - 1) * LIMB_SIZE * 8 + 8)) - 1
#    return mod

def gen_mod(limb_count: int) -> int:
    return (1 << (limb_count * LIMB_SIZE * 8)) - 1

def worst_case_mulmontmax_input(limb_count: int) -> (int, int):
    mod = gen_mod(limb_count)
    r = 1 << (limb_count * LIMB_SIZE * 8)
    r_inv = pow(-mod, -1, r)
    
    # TODO this is the "pseudo worst-case" input for the CIOS algorithm from gnark-crypto
    # It does a final subtraction, but the final check if output>modulus is determined because
    # the output occupies limb_count + 1 limbs
    return 1, 1

def worst_case_addmodmax_inputs(limb_count: int) -> (int, int):
    mod = gen_mod(limb_count)
    x = mod - 2

    return x, 1

def worst_case_submodmax_inputs(limb_count: int) -> (int, int):
    return 1, 0

# generate the slowest inputs for the maximum modulus representable by limb_count limbs
def gen_evmmax_worst_input(op: str, limb_count: int) -> (int, int):
    if op == "MULMONTX":
        # TODO generate inputs to make the final subtraction happen
        return worst_case_mulmontmax_input(limb_count)
    elif op == "ADDMODX":
        return worst_case_addmodmax_inputs(limb_count)
    elif op == "SUBMODX":
        return worst_case_submodmax_inputs(limb_count)
    else:
        raise Exception("unknown evmmax arith op")

def gen_evmmax_op(op: str, out_slot: int, x_slot: int, y_slot: int) -> str:
    return EVMMAX_ARITH_OPS[op] + gen_encode_evmmax_bytes(out_slot, x_slot, y_slot)

MAX_CONTRACT_SIZE = 24576

def gen_arith_loop_benchmark(op: str, limb_count: str) -> str:
    mod = gen_mod(limb_count)
    setmod = gen_setmod(0, mod)

    # mod_mem = limb_count * 8 * 4 # the offset of the first word beyond the end of the last slot we will use
    # expand_memory = gen_mstore_int(end_mem, 0)

    x_input, y_input = gen_evmmax_worst_input(op, limb_count)
    store_inputs = gen_mstore_evmmax_elem(1, x_input, limb_count) + gen_mstore_evmmax_elem(2, y_input, limb_count)
    x2 = (mod - 1) >> 63
    y2 = (mod - 1) >> 63
    store_inputs2 = gen_mstore_evmmax_elem(3, x_input, limb_count) + gen_mstore_evmmax_elem(4, y_input, limb_count)
    
    bench_start = setmod + store_inputs + store_inputs2
    loop_body = ""

    empty_bench_len = int(len(gen_loop().format(bench_start, "", gen_push_int(258))) / 2)
    free_size = MAX_CONTRACT_SIZE - empty_bench_len
    iter_size = 4 # EVMMAX_ARITH_OPCODE + 3 byte immediate
    # iter_count = math.floor(free_size / 5)
    # import pdb; pdb.set_trace()
    iter_count = 5000

    inner_loop_evmmax_op_count = 0

    for i in range(iter_count):
        loop_body += gen_evmmax_op(op, 0, 1, 2)
        inner_loop_evmmax_op_count += 1

    res = gen_loop().format(bench_start, loop_body, gen_push_int(int(len(bench_start) / 2) + 33))
    # assert len(res) / 2 <= MAX_CONTRACT_SIZE, "benchmark greater than max contract size"
    return res, inner_loop_evmmax_op_count 

def gen_loop() -> str:
    return "{}7fffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff015b{}60010180{}57"

def gen_push3_pop_loop_benchmark(count: int) -> str:
    loop_body = ""
    for i in range(count):
        if i % 2 == 0:
            loop_body += gen_push_literal(gen_encode_evmmax_bytes(1, 2, 5))
        else:
            loop_body += gen_push_literal(gen_encode_evmmax_bytes(3, 4, 5))

        loop_body += EVM_OPS["POP"]

    return gen_loop().format("", loop_body, gen_push_int(33))

# bench some evm bytecode and return the runtime in ns

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

def bench_geth_evmmax(arith_op_name: str, limb_count: int) -> (int, int):
    bench_code, evmmax_op_count = gen_arith_loop_benchmark(arith_op_name, limb_count)

    return bench_geth(bench_code), evmmax_op_count

def bench_run(benches):
    for op_name, limb_count_min, limb_count_max in benches:
        for i in range(limb_count_min, limb_count_max + 1):
            evmmax_bench_time, evmmax_op_count = bench_geth_evmmax(op_name, i) 

            setmod_est_time = 0 # TODO

            est_time = math.ceil((evmmax_bench_time) / (evmmax_op_count * LOOP_ITERATIONS))
            #print("{} - {} limbs - {} ns/op".format(arith_op_name, limb_count, est_time))
            print("{},{},{}".format(op_name, limb_count, est_time))

def default_run():
    #print("op name, limb count, estimated runtime (ns)")
    print("op name, input size (in 8-byte increments), opcode runtime est (ns)")
    for arith_op_name in ["ADDMODX", "SUBMODX", "MULMONTX"]:
        for limb_count in range(1, 17):
            for i in range(5):
                evmmax_bench_time, evmmax_op_count = bench_geth_evmmax(arith_op_name, limb_count) 

                #push3_pop_bench_time = bench_geth(gen_push3_pop_loop_benchmark(evmmax_op_count))
                setmod_est_time = 0 # TODO

                est_time = round((evmmax_bench_time - setmod_est_time) / (evmmax_op_count * LOOP_ITERATIONS), 2)
                #print("{} - {} limbs - {} ns/op".format(arith_op_name, limb_count, est_time))
                print("{},{},{}".format(arith_op_name, limb_count, est_time))
        #print()

def bench_one(op, start, end):
    for limb_count in range(start, end+1):
        for i in range(5):
            evmmax_bench_time, evmmax_op_count = bench_geth_evmmax(op, limb_count) 

            setmod_est_time = 0 # TODO

            est_time = round((evmmax_bench_time - setmod_est_time) / (evmmax_op_count * LOOP_ITERATIONS), 2)
            print("{},{},{}".format(op, limb_count, est_time))

if __name__ == "__main__":
    if len(sys.argv) == 1:
        default_run()
    elif len(sys.argv) == 2:
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
    elif len(sys.argv) == 4:
        op = sys.argv[1]
        start = int(sys.argv[2])
        end = int(sys.argv[3])
        bench_one(op, start, end)
    else:
        print("too many args")


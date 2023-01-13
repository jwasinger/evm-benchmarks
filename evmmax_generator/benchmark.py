import sys
import os
import subprocess
import json
import math

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
    elif exec_time.endswith("\\xc2\\xb5s"):
        import pdb; pdb.set_trace()
        exec_time = int(float(exec_time[:-9]) * 1000)
    elif exec_time.endswith("s"):
        exec_time = int(float(exec_time[:-1]) * 1000000 * 1000)
    else:
        raise Exception("unknown timestamp ending: {}".format(exec_time))

    return exec_time

def bench_run(benches):
    for op_name, limb_count_min, limb_count_max in benches:
        for i in range(limb_count_min, limb_count_max + 1):
            evmmax_bench_time, evmmax_op_count = bench_geth_evmmax(op_name, i) 

            setmod_est_time = 0 # TODO

            est_time = math.ceil((evmmax_bench_time) / (evmmax_op_count * LOOP_ITERATIONS))
            #print("{} - {} limbs - {} ns/op".format(arith_op_name, limb_count, est_time))
            print("{},{},{}".format(op_name, limb_count, est_time))

LOOP_ITERATIONS = 255

def default_run():
    # TODO remove previous benchmarks dir content

    for arith_op_name in ["ADDMODX", "SUBMODX", "MULMONTX"]:
        for limb_count in range(1, 16):
            benchmark_file = os.path.join(os.getcwd(), "benchmarks/{}-{}.json".format(arith_op_name,limb_count))
            with open(benchmark_file) as f:
                benchmark = json.load(f)
                for i in range(5):
                    import pdb; pdb.set_trace()
                    exec_time = bench_geth(benchmark['bench_code'])
                    evmmax_op_count = benchmark['evmmax_op_count']

                    setmod_est_time = 0 # TODO
                    est_time = math.ceil((exec_time) / (evmmax_op_count * LOOP_ITERATIONS))
                    print("{},{},{}".format(arith_op_name, limb_count, est_time))

    #print("op name, limb count, estimated runtime (ns)")
    print("op name, input size (in 8-byte increments), opcode runtime est (ns)")
    for arith_op_name in ["ADDMODX", "SUBMODX", "MULMONTX"]:
        for limb_count in range(1, 16):
            bench_code, evmmax_op_count = gen_arith_loop_benchmark(arith_op_name, limb_count)

            with open('benchmarks/{}-{}.hex'.format(arith_op_name, limb_count), 'w') as f:
                f.write(bench_code)

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


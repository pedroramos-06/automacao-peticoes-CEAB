import argparse
from ceab.worker import run_worker

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Worker CEAB/DJ (iniciado pelo painel).')
    parser.add_argument('--db',required=True)
    parser.add_argument('--run',type=int,required=True)
    args = parser.parse_args()
    run_worker(args.db,args.run)

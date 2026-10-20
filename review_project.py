"""Reopen durable results without reimporting original datasets."""
import argparse,time,webbrowser
from web_maps import LocalMapServer

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--folder',required=True);args=parser.parse_args();server=LocalMapServer(args.folder)
    try:
        print('Review map:',server.url,flush=True);webbrowser.open(server.url)
        while True:time.sleep(1)
    except KeyboardInterrupt:pass
    finally:server.close()
if __name__=='__main__':main()

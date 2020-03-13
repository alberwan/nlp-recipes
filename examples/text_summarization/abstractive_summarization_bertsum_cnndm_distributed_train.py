# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License.

import argparse
import os
import pickle
import sys
import time
import torch
import torch.distributed as dist
import torch.multiprocessing as mp

# torch.set_printoptions(threshold=5000)
from tempfile import TemporaryDirectory

nlp_path = os.path.abspath("../../")
if nlp_path not in sys.path:
    sys.path.insert(0, nlp_path)


from utils_nlp.models.transformers.abstractive_summarization_bertsum import (
    BertSumAbs,
    BertSumAbsProcessor,
    validate,
)
from utils_nlp.dataset.cnndm import CNNDMSummarizationDataset

os.environ["NCCL_IB_DISABLE"] = "0"
# os.environ["NCCL_DEBUG"] = "INFO"
os.environ["NCCL_DEBUG_SUBSYS"] = "ALL"
# os.environ["MASTER_PORT"] = "29952"
# os.environ["MASTER_ADDR"] = "172.12.0.6"
# os.environ['NCCL_SOCKET_IFNAME'] = 'lo'


parser = argparse.ArgumentParser()
parser.add_argument(
    "--rank", type=int, default=0, help="The rank of the current node in the cluster"
)
parser.add_argument(
    "--dist_url",
    type=str,
    default="tcp://127.0.0.1:29500",
    help="URL specifying how to initialize the process groupi.",
)
parser.add_argument(
    "--node_count", type=int, default=1, help="Number of nodes in the cluster."
)

parser.add_argument(
    "--cache_dir",
    type=str,
    default="./abstemp",
    help="Directory to cache the tokenizer.",
)
parser.add_argument(
    "--data_dir",
    type=str,
    default="./",
    help="Directory to download the preprocessed data.",
)
parser.add_argument(
    "--output_dir",
    type=str,
    default="./abstemp",
    help="Directory to save the output model and prediction results.",
)
parser.add_argument(
    "--quick_run",
    type=str.lower,
    default="false",
    choices=["true", "false"],
    help="Whether to have a quick run",
)
parser.add_argument(
    "--model_name",
    type=str,
    default="bert-base-uncased",
    help='Transformer model used in the summarization model, only \
                        "bert-uncased" is supported so far.',
)
parser.add_argument(
    "--lr_bert", type=float, default=2e-3, help="Learning rate for the BERT encoder."
)
parser.add_argument(
    "--lr_dec", type=float, default=2e-1, help="Learning rate for the decoder."
)
parser.add_argument(
    "--batch_size",
    type=int,
    default=5,
    help="batch size in terms of input token numbers in training",
)
parser.add_argument(
    "--max_pos_length",
    type=int,
    default=512,
    help="maximum input length in terms of input token numbers in training",
)
parser.add_argument(
    "--max_steps",
    type=int,
    default=5e4,
    help="Maximum number of training steps run in training. \ 
        If quick_run is set, it's not used.",
)
parser.add_argument(
    "--warmup_steps_bert",
    type=int,
    default=2e4,
    help="Warm-up number of training steps run in training for the encoder. \
        If quick_run is set, it's not used.",
)
parser.add_argument(
    "--warmup_steps_dec",
    type=int,
    default=1e4,
    help="Warm-up number of training steps run in training for the decoder. \
        If quick_run is set, it's not used.",
)
parser.add_argument(
    "--summary_filename",
    type=str,
    default="generated_summaries.txt",
    help="Summary file name generated by prediction for evaluation.",
)
parser.add_argument(
    "--model_filename",
    type=str,
    default="dist_abssum_model.pt",
    help="model file name saved for evaluation.",
)
parser.add_argument(
    "--checkpoint_filename",
    type=str,
    default=None,
    help="filename of a checkpoint where the trainging resumes from. \
                            default path is at cache_dir",
)
parser.add_argument(
    "--report_every",
    type=int,
    default=10,
    help="number of steps between each loss report",
)
parser.add_argument(
    "--save_every",
    type=int,
    default=500,
    help="number of steps between each model save and validation",
)
parser.add_argument(
    "--fp16",
    type=str.lower,
    default="false",
    choices=["true", "false"],
    help="Whether to use mixed precision training",
)
parser.add_argument(
    "--fp16_opt_level",
    type=str.upper,
    default="O2",
    choices=["O0", "O1", "O2", "O3"],
    help="optimization level, refer to \
         https://nvidia.github.io/apex/amp.html#opt-levels for details ",
)


def main():

    args = parser.parse_args()

    print("NCCL_IB_DISABLE: {}".format(os.getenv("NCCL_IB_DISABLE")))
    print("quick_run is {}".format(args.quick_run))
    print("output_dir is {}".format(args.output_dir))
    print("data_dir is {}".format(args.data_dir))
    print("cache_dir is {}".format(args.cache_dir))

    train_dataset, test_dataset = CNNDMSummarizationDataset(
        top_n=-1, local_cache_path=args.data_dir, prepare_extractive=False
    )

    ngpus_per_node = torch.cuda.device_count()
    processor = BertSumAbsProcessor(
        cache_dir=args.cache_dir, max_src_len=args.max_pos_length
    )
    summarizer = BertSumAbs(
        processor, cache_dir=args.cache_dir, max_pos_length=args.max_pos_length
    )
    mp.spawn(
        main_worker,
        nprocs=ngpus_per_node,
        args=(ngpus_per_node, summarizer, train_dataset, test_dataset, args),
    )


def main_worker(
    local_rank, ngpus_per_node, summarizer, train_dataset, test_dataset, args
):
    rank = args.rank * ngpus_per_node + local_rank
    world_size = args.node_count * ngpus_per_node
    print("world_size is {}".format(world_size))
    print("local_rank is {} and rank is {}".format(local_rank, rank))

    torch.distributed.init_process_group(
        backend="nccl", init_method=args.dist_url, world_size=world_size, rank=rank,
    )

    # return
    ## should not load checkpoint from this place, otherwise, huge memory increase
    if args.checkpoint_filename:
        checkpoint = os.path.join(args.cache_dir, args.checkpoint_filename)
    else:
        checkpoint = None
    # train_sum_dataset, test_sum_dataset = load_processed_cnndm_abs(args.data_dir)
    def this_validate(class_obj):
        return validate(class_obj, test_dataset)

    if rank not in [-1, 0]:
        save_every = -1
        this_validate = None
    else:
        save_every = args.save_every

    fp16 = args.fp16.lower() == "true"
    print("fp16 is {}".format(fp16))
    # total number of steps for training
    MAX_STEPS = 50
    SAVE_EVERY = 50
    REPORT_EVERY = 10
    # number of steps for warm up
    WARMUP_STEPS_BERT = MAX_STEPS
    WARMUP_STEPS_DEC = MAX_STEPS
    if args.quick_run.lower() == "false":
        MAX_STEPS = args.max_steps
        WARMUP_STEPS_BERT = args.warmup_steps_bert
        WARMUP_STEPS_DEC = args.warmup_steps_dec
        SAVE_EVERY = args.save_every
        REPORT_EVERY = args.report_every

    print("max steps is {}".format(MAX_STEPS))
    print("warmup steps for encoder bert is {}".format(WARMUP_STEPS_BERT))
    print("warmup steps for decoder is {}".format(WARMUP_STEPS_DEC))
    start = time.time()

    # summarizer.model.load_checkpoint(checkpoint['model'])
    summarizer.fit(
        train_dataset,
        world_size=world_size,
        num_gpus=None,
        local_rank=local_rank,
        rank=rank,
        batch_size=args.batch_size,
        max_steps=MAX_STEPS / world_size,
        learning_rate_bert=args.lr_bert,
        learning_rate_dec=args.lr_dec,
        warmup_steps_bert=WARMUP_STEPS_BERT,
        warmup_steps_dec=WARMUP_STEPS_DEC,
        save_every=SAVE_EVERY,
        report_every=REPORT_EVERY,
        validation_function=this_validate,
        fp16=fp16,
        fp16_opt_level=args.fp16_opt_level,
        checkpoint=checkpoint,
    )

    end = time.time()
    print("rank {0}, duration {1:.6f}s".format(rank, end - start))
    if rank == 0 or local_rank == -1:
        saved_model_path = os.path.join(
            args.output_dir, "{}_step{}".format(args.model_filename, MAX_STEPS)
        )
        summarizer.save_model(MAX_STEPS, saved_model_path)
        top_n = 8
        prediction = summarizer.predict(
            test_dataset.shorten(top_n=top_n), batch_size=4, num_gpus=ngpus_per_node
        )
        print(prediction[0])

        def _write_list_to_file(list_items, filename):
            with open(filename, "w") as filehandle:
                # for cnt, line in enumerate(filehandle):
                for item in list_items:
                    filehandle.write("%s\n" % item)

        print("writing generated summaries")
        _write_list_to_file(
            prediction, os.path.join(args.output_dir, args.summary_filename)
        )

    # only use the following line when you use your own cluster.
    # AML distributed training run cleanup for you.
    dist.destroy_process_group()


if __name__ == "__main__":
    main()

import os
import numpy as np
import pandas as pd
import logging
from torch.utils.data import dataset
from datasets import load_dataset, load_metric
from transformers import (
    T5ForConditionalGeneration,
    T5Tokenizer,
    Seq2SeqTrainingArguments,
    Seq2SeqTrainer,
    set_seed,
    EarlyStoppingCallback
)
from transformers.trainer_utils import get_last_checkpoint

logger = logging.getLogger(__name__)


class Seq2SeqDataset(dataset.Dataset):
    def __init__(self, path_df=None, tokenizer=None, max_source_length=512, max_target_length=256, prefix=None, 
    ignore_pad_token_for_loss=True, padding="max_length", max_samples=None, split="train"):
        self.text_column = "article"
        self.summary_column = "highlights"
        self.tokenizer = tokenizer
        self.max_source_length = max_source_length
        self.max_target_length = max_target_length
        self.prefix = prefix if prefix is not None else ""
        self.ignore_pad_token_for_loss = ignore_pad_token_for_loss
        self.padding = padding
        if path_df is None:
            self.df = load_dataset('cnn_dailymail', '3.0.0', ignore_verifications=True, split=split).to_pandas()
        else:
            self.df = pd.read_csv(path_df)

        if max_samples is not None:
            self.df = self.df.sample(n=max_samples, random_state=42).reset_index(drop=True)


    def __len__(self):
        return len(self.df)

    def __getitem__(self, index):
        input_text = self.df.loc[index, self.text_column]
        output_text = self.df.loc[index, self.summary_column]
        
        input_text = self.prefix + input_text
        model_inputs = self.tokenizer(input_text, max_length=self.max_source_length, padding=self.padding, truncation=True)

        # Setup the tokenizer for target
        with self.tokenizer.as_target_tokenizer():
            label = self.tokenizer(output_text, max_length=self.max_target_length, padding=self.padding, truncation=True)

        # If we are padding here, replace all tokenizer.pad_token_id in the label by -100 when we want to ignore padding in the loss.
        if self.padding == "max_length" and self.ignore_pad_token_for_loss:
            label["input_ids"] = [-100 if l == self.tokenizer.pad_token_id else l for l in label["input_ids"]]

        model_inputs["labels"] = label["input_ids"]
        return model_inputs


def main():
    # Arguments
    import argparse
    parser = argparse.ArgumentParser()
    # Input
    parser.add_argument("--train_file", type=str, default=None, help="Path to train file.")
    parser.add_argument("--validation_file", type=str, default=None, help="Path to valid file.")
    parser.add_argument("--test_file", type=str, default=None, help="Path to test file.")
    # Model and Training
    parser.add_argument("--model_name_or_path", default="t5-base", type=str, help="Path to pre-trained model or name of huggingface/transformers model")
    parser.add_argument("--num_epochs", default=10, type=int, help="Number of epochs to train for")
    parser.add_argument("--max_source_length", default=768, type=int, help="Maximum length of the source sequence")
    parser.add_argument("--max_target_length", default=256, type=int, help="Maximum length of the target sequence")
    parser.add_argument("--do_train", default=True, action="store_true", help="Whether to run training.")
    parser.add_argument("--do_eval", default=False, action="store_true", help="Whether to run eval on the dev set.")
    parser.add_argument("--do_predict", default=False, action="store_true", help="Whether to run predictions on the test set.")
    parser.add_argument("--logging_steps", type=int, default=100, help="Log every X updates steps.")
    parser.add_argument("--eval_steps", type=int, default=1000, help="Run evaluation every X steps.")
    parser.add_argument("--save_steps", type=int, default=1000, help="Save checkpoint every X updates steps.")
    parser.add_argument("--batch_size", type=int, default=4, help="Batch size per GPU/CPU for training.")
    parser.add_argument("--batch_size_val", type=int, default=4, help="Batch size per GPU/CPU for validation.")
    parser.add_argument("--warmup_steps", type=int, default=200, help="Linear warmup over warmup_steps.")
    parser.add_argument("--accum_steps", type=int, default=2, help="Accumulate gradients across X steps.")
    parser.add_argument("--num_beams", type=int, default=4, help="Beam size.")
    parser.add_argument("--fp16", default=False, action="store_true", help="Whether to use 16-bit (mixed) precision (through NVIDIA apex) instead of 32-bit")
    parser.add_argument("--overwrite_output_dir", default=False, action="store_true", help="Overwrite the content of the output directory")
    parser.add_argument("--early_stopping_patience", type=int, default=10, help="Early stopping patience.")
    # For debugging
    parser.add_argument("--max_train_samples", type=int, default=None, help="Maximum number of training samples to use.")
    parser.add_argument("--max_eval_samples", type=int, default=None, help="Maximum number of evaluation samples to use.")
    parser.add_argument("--max_predict_samples", type=int, default=None, help="Maximum number of prediction samples to use.")
    # Output
    parser.add_argument("--output_dir", default="dnn_dailymail_t5_base_output", type=str, help="The output directory where the model predictions and checkpoints will be written.")
    # Setting save 2 last checkpoint and save best checkpoint
    parser.add_argument("--save_total_limit", type=int, default=2, help="If we save total_limit checkpoints, delete the older checkpoints")
    parser.add_argument("--load_best_model_at_end", default=True, action="store_true", help="Save only the best checkpoint in the output directory")
    config = parser.parse_args()
    
    # Load model and tokenizer
    model = T5ForConditionalGeneration.from_pretrained(config.model_name_or_path)
    tokenizer = T5Tokenizer.from_pretrained(config.model_name_or_path)
    # special_tokens = ["[language]", "[\language]", "[correct]", "[\correct]", "[problem]", "[\problem]", "[incorrect]", "[\incorrect]"]
    # tokenizer.add_special_tokens({"additional_special_tokens": special_tokens})
    # model.resize_token_embeddings(len(tokenizer))
    
    training_args = Seq2SeqTrainingArguments(
        output_dir=config.output_dir,
        do_train=config.do_train,
        do_eval=config.do_eval,
        do_predict=config.do_predict,
        num_train_epochs=config.num_epochs,
        predict_with_generate=True,
        evaluation_strategy="steps",
        per_device_train_batch_size=config.batch_size,
        per_device_eval_batch_size=config.batch_size_val,
        fp16=config.fp16,
        # half_precision_backend="apex",
        logging_steps=config.logging_steps,
        eval_steps=config.eval_steps,
        save_steps=config.save_steps,
        warmup_steps=config.warmup_steps,
        gradient_accumulation_steps=config.accum_steps,
        overwrite_output_dir=config.overwrite_output_dir,
        save_total_limit=config.save_total_limit,
        load_best_model_at_end=config.load_best_model_at_end,
        metric_for_best_model="rouge1",
    )
    print("------- Training arguments -------")
    print(training_args)

    # Detecting last checkpoint.
    last_checkpoint = None
    if os.path.isdir(training_args.output_dir) and training_args.do_train and not training_args.overwrite_output_dir:
        last_checkpoint = get_last_checkpoint(training_args.output_dir)
        if last_checkpoint is None and len(os.listdir(training_args.output_dir)) > 0:
            raise ValueError(
                f"Output directory ({training_args.output_dir}) already exists and is not empty. "
                "Use --overwrite_output_dir to overcome."
            )
        elif last_checkpoint is not None and training_args.resume_from_checkpoint is None:
            logger.info(
                f"Checkpoint detected, resuming training at {last_checkpoint}. To avoid this behavior, change "
                "the `--output_dir` or add `--overwrite_output_dir` to train from scratch."
            )

    # Set seed before initializing model.
    set_seed(training_args.seed)

    # Metric
    rouge = load_metric("rouge")

    def compute_metrics(eval_preds):
        preds, labels = eval_preds
        if isinstance(preds, tuple):
            preds = preds[0]
        decoded_preds = tokenizer.batch_decode(preds, skip_special_tokens=True)
        # Replace -100 in the labels as we can't decode them.
        labels = np.where(labels != -100, labels, tokenizer.pad_token_id)
        decoded_labels = tokenizer.batch_decode(labels, skip_special_tokens=True)

        result = rouge.compute(predictions=decoded_preds, references=decoded_labels)
        # Extract a few results from ROUGE
        result = {key: value.mid.fmeasure * 100 for key, value in result.items()}

        prediction_lens = [np.count_nonzero(pred != tokenizer.pad_token_id) for pred in preds]
        result["gen_len"] = np.mean(prediction_lens)
        result = {k: round(v, 4) for k, v in result.items()}
        return result
    
    # Load dataset
    train_dataset = Seq2SeqDataset(
        tokenizer=tokenizer,
        max_source_length=config.max_source_length,
        max_target_length=config.max_target_length,
        max_samples=config.max_train_samples,
        prefix="Summary this article: ",
        split="train",
    )
    eval_dataset = Seq2SeqDataset(
        tokenizer=tokenizer,
        max_source_length=config.max_source_length,
        max_target_length=config.max_target_length,
        max_samples=config.max_eval_samples,
        prefix="Summary this article: ",
        split="validation",
    )
    predict_dataset = Seq2SeqDataset(
        tokenizer=tokenizer,
        max_source_length=config.max_source_length,
        max_target_length=config.max_target_length,
        max_samples=config.max_predict_samples,
        prefix="Summary this article: ",
        split="test",
    )

    trainer = Seq2SeqTrainer(
        model=model,
        args=training_args,
        train_dataset=train_dataset if training_args.do_train else None,
        eval_dataset=eval_dataset if training_args.do_eval else None,
        tokenizer=tokenizer,
        compute_metrics=compute_metrics if training_args.predict_with_generate else None,
        callbacks=[EarlyStoppingCallback(early_stopping_patience=config.early_stopping_patience)]
    )

    params = sum(p.numel() for p in model.parameters() if p.requires_grad)
    print(f"Total trainable parameters: {params}")

    # Training
    if training_args.do_train:
        checkpoint = None
        if training_args.resume_from_checkpoint is not None:
            checkpoint = training_args.resume_from_checkpoint
        elif last_checkpoint is not None:
            checkpoint = last_checkpoint
        train_result = trainer.train(resume_from_checkpoint=checkpoint)
        trainer.save_model()  # Saves the tokenizer too for easy upload

        metrics = train_result.metrics
        max_train_samples = (
            config.max_train_samples if config.max_train_samples is not None else len(train_dataset)
        )
        metrics["train_samples"] = min(max_train_samples, len(train_dataset))

        trainer.log_metrics("train", metrics)
        trainer.save_metrics("train", metrics)
        trainer.save_state()

    # Evaluation
    results = {}
    num_beams = config.num_beams if config.num_beams is not None else training_args.generation_num_beams
    if training_args.do_eval:
        logger.info("*** Evaluate ***")
        metrics = trainer.evaluate(max_length=config.max_target_length, num_beams=num_beams, metric_key_prefix="eval")
        max_eval_samples = config.max_eval_samples if config.max_eval_samples is not None else len(eval_dataset)
        metrics["eval_samples"] = min(max_eval_samples, len(eval_dataset))

        trainer.log_metrics("eval", metrics)
        trainer.save_metrics("eval", metrics)

    if training_args.do_predict:
        logger.info("*** Predict ***")

        predict_results = trainer.predict(
            predict_dataset, metric_key_prefix="predict", max_length=config.max_target_length, num_beams=num_beams
        )
        metrics = predict_results.metrics
        max_predict_samples = (
            config.max_predict_samples if config.max_predict_samples is not None else len(predict_dataset)
        )
        metrics["predict_samples"] = min(max_predict_samples, len(predict_dataset))

        trainer.log_metrics("predict", metrics)
        trainer.save_metrics("predict", metrics)

        if trainer.is_world_process_zero():
            if training_args.predict_with_generate:
                predictions = tokenizer.batch_decode(
                    predict_results.predictions, skip_special_tokens=True, clean_up_tokenization_spaces=True
                )
                predictions = [pred.strip() for pred in predictions]
                output_prediction_file = os.path.join(training_args.output_dir, "generated_predictions.csv")
                predict_df = predict_dataset.df.copy()
                predict_df["prediction"] = predictions
                predict_df.to_csv(output_prediction_file, index=False)


if __name__ == "__main__":
    main()
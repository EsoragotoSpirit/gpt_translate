import yaml
import logging
from pathlib import Path
import aiohttp
import asyncio
from openai import AsyncOpenAI
from tenacity import (
    retry,
    stop_after_attempt,
    wait_random_exponential, # for exponential backoff
)
from fastcore.script import call_parse, Param, store_true, store_false

from gpt_translate.prompts import PromptTemplate
from gpt_translate.loader import remove_markdown_comments, split_markdown, MDPage
from gpt_translate.utils import count_tokens, measure_execution_time, get_md_files, file_is_empty


logging.basicConfig(level=logging.INFO)

client = AsyncOpenAI()
semaphore = asyncio.Semaphore(5)  # Adjust the limit as needed

MAX_CHUNK_TOKENS = 2000
REPLACE = False
REMOVE_COMMENTS = True

@retry(wait=wait_random_exponential(min=1, max=60), stop=stop_after_attempt(6))
async def completion_with_backoff(**kwargs):
    return await client.chat.completions.create(**kwargs)

async def translate_chunk(chunk:str, prompt:PromptTemplate, **model_args):
    """Translate a markdown chunk asynchronously
    chunk: markdown chunk
    prompt: PromptTemplate object
    return: translated chunk
    """
    logging.info(f"Calling OpenAI with {model_args}\nTranslating chunk: {chunk[:100]}...")
    async with semaphore:
        res = await completion_with_backoff(
            messages=prompt.format(md_chunk=chunk), 
            **model_args)
        output = res.choices[0].message.content
        logging.info(f"OpenAI response: {output[:100]}...")
        logging.debug(res.usage)
        return output

@measure_execution_time
async def translate_splitted_md(
        splitted_markdown:list[str], 
        prompt:PromptTemplate, 
        max_chunk_tokens:int=MAX_CHUNK_TOKENS, 
        sep:str="\n\n",
        **model_args,
        )->str:
    """Translate a list of markdown chunks asynchronously
    splitted_markdown: list of markdown chunks
    prompt: PromptTemplate object
    max_chunk_tokens: maximum number of tokens per chunk
    sep: separator between chunks
    model_args: arguments to pass to the completion_with_backoff function
    return: translated markdown file
    """

    tasks = []
    packed_chunks = ""
    packed_chunks_len = 0

    for i, chunk in enumerate(splitted_markdown):
        
        n_tokens = count_tokens(chunk)

        if packed_chunks_len + n_tokens <= max_chunk_tokens:
            logging.debug(f"Packing chunk {i} with {n_tokens} tokens")
            packed_chunks += sep + chunk
            packed_chunks_len += n_tokens
        else:
            logging.debug(f">> Translating {packed_chunks_len} tokens")
            tasks.append(translate_chunk(packed_chunks, prompt, **model_args))
            packed_chunks = chunk
            packed_chunks_len = n_tokens
    
    if packed_chunks:
        logging.debug(f">> Translating {packed_chunks_len} tokens (last chunk)")
        tasks.append(translate_chunk(packed_chunks, prompt, **model_args))

    translated_chunks = await asyncio.gather(*tasks)
    return sep.join(translated_chunks)

class Translator:
    "A class to translate markdown files asynchronously"
    def __init__(self, config_folder, language="ja", max_chunk_tokens:int=MAX_CHUNK_TOKENS):
        self.config_folder = Path(config_folder)
        self.language = language
        self.prompt_template = PromptTemplate.from_files(
            self.config_folder / "system_prompt.txt",
            self.config_folder / "human_prompt.txt",
            self.config_folder / f"language_dicts/{language}.yaml"
        )
        self.max_chunk_tokens = max_chunk_tokens
        with open(self.config_folder / "model_config.yaml", 'r') as file:
            self.model_args = yaml.safe_load(file)
            logging.info(f"Model args: {self.model_args}")
    
    async def translate_file(self, md_file:str, remove_comments:bool=True):
        """Translate a markdown file asynchronously"""
        with open(md_file, "r") as f:
            md_content = f.read()
        if remove_comments:
            logging.info("Removing comments")
            md_content = remove_markdown_comments(md_content)
        md_page = MDPage.create(md_file, md_content)
        chunks = split_markdown(md_page.content)
        translated_content = await translate_splitted_md(
            chunks,
            self.prompt_template,
            max_chunk_tokens=self.max_chunk_tokens,
            **self.model_args)
        translated_page = md_page.from_translated(translated_content, fix_links=False)
        return str(translated_page)

async def _translate_file(
    input_file: str, # File to translate
    out_file: str, # File to save the translated file to
    max_chunk_tokens: int = MAX_CHUNK_TOKENS, # Max tokens per chunk
    replace: bool = REPLACE, # Replace existing file
    language: str = "es", # Language to translate to
    config_folder: str = "./configs", # Config folder
    remove_comments: bool = REMOVE_COMMENTS, # Remove comments
):
    """Translate a markdown file asynchronously"""
    if file_is_empty(input_file):
        raise ValueError(f"File {input_file} is empty")

    # check it is a md file
    if Path(input_file).suffix != ".md":
        raise ValueError(f"File {input_file} is not a markdown file")
    out_file = Path(out_file)
    if out_file.exists() and not replace and not file_is_empty(out_file):
        logging.info(f"File {out_file} already exists. Use --replace to overwrite.")
    else:
        out_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            translator = Translator(config_folder, language, max_chunk_tokens)
            translated_file = await translator.translate_file(input_file, remove_comments)
            with open(out_file, "w") as f:
                f.write(translated_file)
            logging.info(f"Translated file saved to {out_file}")
        except Exception as e:
            logging.error(f"Error translating {input_file}: {e}")

async def _translate_files(
    input_files: list[str], # Files to translate
    input_folder: str, # folder where the file lives
    out_folder: str, # Folder to save the translated files to
    max_chunk_tokens: int = MAX_CHUNK_TOKENS, # Max tokens per chunk
    replace: bool = REPLACE, # Replace existing file
    language: str = "es", # Language to translate to
    config_folder: str = "./configs", # Config folder
    remove_comments: bool = REMOVE_COMMENTS, # Remove comments
):
    input_files = [Path(f) for f in input_files if Path(f).suffix == ".md"]
    input_folder = Path(input_folder)
    out_folder = Path(out_folder)
    if not input_folder.is_dir():
        raise ValueError(f"{input_folder} is not a folder")
    
    tasks = []
    for md_file in input_files:
        out_file = out_folder / md_file.relative_to(input_folder)
        tasks.append(_translate_file(str(md_file), str(out_file), max_chunk_tokens, replace, language, config_folder, remove_comments))
    
    await asyncio.gather(*tasks)

@call_parse
def translate_file(
    input_file: Param("File to translate", str),
    out_file: Param("File to save the translated file to", str),
    max_chunk_tokens: Param("Max tokens per chunk", int) = MAX_CHUNK_TOKENS,
    replace: Param("Replace existing file", store_true) = REPLACE,
    language: Param("Language to translate to", str) = "es",
    config_folder: Param("Config folder", str) = "./configs",
    remove_comments: Param("Remove comments", store_false) = REMOVE_COMMENTS,
):
    asyncio.run(_translate_file(input_file, out_file, max_chunk_tokens, replace, 
                    language, config_folder, remove_comments))

@call_parse
def translate_files(
    input_files: Param("Files to translate", nargs="+"),
    input_folder: Param("Folder to translate", str) = "docs/",
    out_folder: Param("Folder to save the translated files to", str) = "translated/",
    max_chunk_tokens: Param("Max tokens per chunk", int) = MAX_CHUNK_TOKENS,
    replace: Param("Replace existing file", store_true) = REPLACE,
    language: Param("Language to translate to", str) = "es",
    config_folder: Param("Config folder", str) = "./configs",
    remove_comments: Param("Remove comments", store_false) = REMOVE_COMMENTS,
):
    asyncio.run(_translate_files(input_files, input_folder, out_folder, max_chunk_tokens, 
                     replace, language, config_folder, remove_comments))


@call_parse
def translate_folder(
    input_folder: Param("Folder to translate", str),
    out_folder: Param("Folder to save the translated files to", str) = "translated/",
    max_chunk_tokens: Param("Max tokens per chunk", int) = MAX_CHUNK_TOKENS,
    replace: Param("Replace existing files", store_true) = REPLACE,
    language: Param("Language to translate to", str) = "es",
    config_folder: Param("Config folder", str) = "./configs",
    remove_comments: Param("Remove comments", store_false) = REMOVE_COMMENTS,
    limit: Param("Limit number of files to translate", int) = None,
):
    """Translate all markdown files in a folder respecting the folder hierarchy"""
    input_files = get_md_files(input_folder)[:limit]
    logging.info(f"Translating {len(input_files)} files")
    asyncio.run(_translate_files(input_files, input_folder, out_folder, max_chunk_tokens, 
                                 replace, language, config_folder, remove_comments))



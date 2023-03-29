import ast
from collections import Counter
import concurrent.futures
import csv
import io
from pathlib import Path
import shlex
import sqlite3
import time

from deep_translator import GoogleTranslator
import pandas as pd
from shiny import App, reactive, render, ui


app_ui = ui.page_fluid(
    ui.panel_title('Kindle Vocabulary Flashcard Generator'),
    ui.layout_sidebar(
        ui.panel_sidebar(
            ui.p("""Upload your Kindle's vocab.db file and convert your searched words 
                    to flashcards for spaced repetition practice! This app will output text in a format 
                    suitable for import to Anki, Knowt, Quizlet, Brainscape, etc."""
            ),
            ui.p("""You can get your Kindle's 
                    vocab.db by connecting it to your computer and searching in the file system. Translations
                    of the source text to your native language use Google Translate, and will take some time
                    if you have a large vocabulary or if a lot of people are using the site."""
            ),
            ui.p("""If this helps you, please consider buying me a coffee so I will be
                    motivated to keep the site up and running :)   - Connor"""
            ),
            ui.a(
                ui.img(src='github.png', width=45, height=45),
                href='https://github.com/connorhargus/cardle',
            ),
            ui.a(
                ui.img(src='bmc-button.png', width=150, height=45),
                href='https://www.buymeacoffee.com/connorhargus',
            ),
        ),
        ui.panel_main(
            ui.div({"class": "card mb-3"},
                ui.div({"class": "card-body"},
                    ui.row(
                        ui.column(6,
                            ui.input_checkbox("word_translate", "Word translation", True),
                            ui.input_checkbox("usage_translate", "Phrase translation", True),
                            ui.input_checkbox("bold_word", "Bold original word", True),
                            ui.input_checkbox("bold_usage", "Bold original usage", True),
                            ui.input_checkbox("italic_translated_word", "Italic translated word", True),
                            ui.input_checkbox("italic_translated_usage", "Italic translated sentence", False),
                            ui.input_checkbox("html_newlines", "Use HTML text formatting and newlines", True),
                        ),
                        ui.column(6,
                            ui.input_selectize("native_language", "Translate to:",
                                               GoogleTranslator().get_supported_languages(), selected='english'),
                            ui.input_selectize("col_delimiter", "Column delimiter:", ['\\t', '\\n', ';']),
                            ui.input_text("row_delimiter", "Line delimiter:", r'\n\n'),
                            ui.input_numeric("max_usage_length", "Max sentence characters:", 250),
                        ),
                    ),
                    ui.input_file("file1", "Choose a file to upload:", multiple=True),
                    ui.input_action_button("submit", "Convert"),
                ),
            ),
        ),
    ),
    ui.br(),
    ui.output_ui("output_button"),
    ui.output_text_verbatim("output_text"),
)


def server(input, output, session):
    max_size = 50000000  # 50 MB max size for uploaded database
    output_str = reactive.Value('')

    @output
    @render.text
    def output_button():
        return ui.download_button("download_result", "Download Results") if len(output_str()) > 0 else None

    @output
    @render.text
    def output_text():
        return output_str()

    @session.download(filename="vocab.txt")
    def download_result():
        with io.BytesIO() as buf:
            buf.write(output_str().encode())
            yield buf.getvalue()

    @reactive.Effect
    @reactive.event(input.submit)
    def convert():
        file_info = input.file1()
        if not file_info:
            return

        if file_info[0]["size"] > max_size:
            modal = ui.modal(
                "This file is too large (>100MB). You must be reading a lot!",
                title="Conversion failed 😢",
                easy_close=True,
                footer=None,
            )
            ui.modal_show(modal)
            return

        datapath = file_info[0]["datapath"]

        con = sqlite3.connect(datapath)
        vocab = pd.read_sql_query('SELECT word_key, usage FROM LOOKUPS', con)
        con.close()

        # Create columns for original language, word looked up, number of times word was looked up, and sentence length
        vocab['from_lang'] = vocab['word_key'].str[:2]
        vocab['word'] = vocab['word_key'].str[3:]
        vocab = vocab.drop(['word_key'], axis=1)
        word_counter = Counter(list(vocab['word']))
        vocab['lookups'] = vocab['word'].map(word_counter)
        vocab['length'] = vocab['usage'].str.len()

        # Keep only sentences reasonably short
        vocab = vocab[vocab['length'] < input.max_usage_length()]

        # Sort by language and then prioritize by number of times looked up
        vocab.sort_values(['from_lang', 'lookups', 'length'], ascending=[True, False, True], inplace=True)
        vocab = vocab.drop_duplicates(subset=['word'], keep='first')

        # vocab = vocab[vocab['lookups'] > 1]  # Keep only words which have been looked up more than once
        # vocab = vocab[:500]  # Shrink the dataframe when debugging this code to avoid unnecessary translate API calls.

        vocab = vocab.reset_index(drop=True)

        if input.word_translate() or input.usage_translate():
            with ui.Progress(min=1, max=len(vocab)) as p:
                p.set(message="Translation in progress", detail="Contacting Google Translate...")

                # Function to apply to word and usage columns in dataframe to translate to to_lang via Google Translate
                def translate_(i, row, key, to_lang):
                    translator = GoogleTranslator(source=row['from_lang'], target=to_lang)
                    result = translator.translate(str(row[key]))
                    return result

                def thread_translate_(vocab, key, to_lang):
                    with concurrent.futures.ThreadPoolExecutor() as executor:
                        results = [executor.submit(translate_, i, row, key, to_lang) for i, row in vocab.iterrows()]
                        i = 0
                        for result in concurrent.futures.as_completed(results):
                            i += 1
                            message = f"Translation in progress"
                            p.set(i, message=result.result(), detail=message)
                        return results

                start = time.perf_counter()

                to_lang = GoogleTranslator().get_supported_languages(as_dict=True)[input.native_language()]
                # Perform translations (English: en, Chinese: zh, Portuguese: pt)
                if input.word_translate():
                    results = thread_translate_(vocab, 'word', to_lang)
                    vocab['word_translated'] = [result.result() for result in results]

                if input.word_translate():
                    results = thread_translate_(vocab, 'usage', to_lang)
                    vocab['usage_translated'] = [result.result() for result in results]

                finish = time.perf_counter()
                print(f'Finished in {finish - start} second(s)')

        newline = '\n' if not input.html_newlines() else '<br>'
        italic = ('*', '*') if not input.html_newlines() else ('<em>', '</em>')
        bold = ('**', '**') if not input.html_newlines() else ('<b>', '</b>')

        # Make cloze usages by filling in word with blanks
        vocab['usage'] = vocab.apply(lambda x: x['usage'].replace(x['word'], '_____'), axis=1)

        vocab['word'] = vocab['word'].str.lower()
        if input.bold_word():
            vocab['word'] = bold[0] + vocab['word'].str.lower() + bold[1]

        vocab['usage'] = vocab['usage'].str.rstrip()
        if input.bold_usage():
            vocab['usage'] = bold[0] + vocab['usage'] + bold[1]

        if input.word_translate():
            vocab['word_translated'] = vocab['word_translated'].str.lower()
            if input.italic_translated_word():
                vocab['word_translated'] = italic[0] + vocab['word_translated'] + italic[1]

        if input.usage_translate():
            if input.italic_translated_usage():
                vocab['usage_translated'] = italic[0] + vocab['usage_translated'] + italic[1]

        vocab['definition'] = vocab['usage'] + (newline + vocab['word_translated'] if input.word_translate() else '') \
                                + (newline + vocab['usage_translated'] if input.usage_translate() else '')
        vocab = vocab[['word', 'definition']]

        # Dealing with \\ characters interpreted by input file:
        # see https://stackoverflow.com/questions/54410812/how-do-you-input-escape-sequences-in-python
        escape_col_delimiter = ast.literal_eval(shlex.quote(input.col_delimiter()))
        escape_row_delimiter = ast.literal_eval(shlex.quote(input.row_delimiter()))

        # Output to a csv format suitable for copy and paste into flashcard website
        vocab_string = vocab.to_csv(None, sep=escape_col_delimiter, header=False, index=False,
                                    lineterminator=escape_row_delimiter,
                                    quoting=csv.QUOTE_NONE, quotechar="", escapechar=" ")

        output_str.set(vocab_string)


static_dir = Path(__file__).parent / "static"
app = App(app_ui, server, static_assets=static_dir)

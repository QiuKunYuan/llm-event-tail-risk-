"""Build the illustrated proposal and a reviewed, self-contained LaTeX bundle.

The user's old thesis is private. Only its unmodified, docstrip-generated
BIThesis class is reused, with LPPL attribution. No old chapters, images,
fonts, signatures or bibliography are imported. XeLaTeX shell escape is off.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from zipfile import ZipFile, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'docs/开题报告/北理工专硕/latex'
CLASS_HASH = '21ab1e018f9fcb9bf522d9d508e88b4d65697e688980ca31dfb94e8fa6cd51e6'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def escape(text):
    replacements = {'\\': r'\textbackslash{}', '&': r'\&', '%': r'\%',
                    '$': r'\$', '#': r'\#', '_': r'\_', '{': r'\{',
                    '}': r'\}', '~': r'\textasciitilde{}', '^': r'\textasciicircum{}'}
    return ''.join(replacements.get(char, char) for char in text)


def bibliography(destination):
    review = (ROOT / 'docs/开题报告/北理工专硕/文献综述-邱琨原.md').read_text(encoding='utf-8')
    section = review.split('## 参考文献', 1)[1]
    entries = re.findall(r'^\[(\d+)\]\s+(.+)$', section, flags=re.M)
    if [int(number) for number, _ in entries] != list(range(1, 35)):
        raise ValueError('Expected the maintained 34-reference bibliography')
    parts = [r'\begin{thebibliography}{99}', r'\addcontentsline{toc}{chapter}{参考文献}']
    for number, text in entries:
        segments = re.split(r'(https?://[^\s]+)', text)
        formatted = []
        for segment in segments:
            if segment.startswith(('http://', 'https://')):
                url = segment.rstrip('。.,，')
                formatted.append(r'\url{' + url + '}' + escape(segment[len(url):]))
            else:
                formatted.append(escape(segment))
        parts.append(r'\bibitem{r' + number + '} ' + ''.join(formatted))
    parts.append(r'\end{thebibliography}')
    (destination / 'references.bbl').write_text('\n'.join(parts) + '\n', encoding='utf-8')


def data_tables(destination):
    facts = json.loads((SOURCE / 'data/audit-summary.json').read_text())
    years = facts['publication_year_rows']
    if sum(years.values()) != facts['shareholder_rows']:
        raise ValueError('Publication counts do not match audited raw total')
    lines = ['year wan count']
    for year, count in years.items():
        lines.append(f'{year} {count / 10000:.8f} {count}')
    (destination / 'data/yearly.dat').write_text('\n'.join(lines) + '\n')
    lines = ['pct field label']
    for field, label in [('InfoSource', '信息来源'), ('A_StkCd', 'A股代码'),
                         ('GDID', '股东ID'), ('PctTrdShr', '流通股比例')]:
        count = facts['shareholder_missing'][field]
        lines.append(f'{100 * count / facts["shareholder_rows"]:.8f} {label} {count:,}条')
    (destination / 'data/missing.dat').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return facts


def prepare(template, directory):
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    for folder in ('sections', 'methods', 'figures', 'data'):
        (directory / folder).mkdir()
    for path in SOURCE.rglob('*'):
        if path.is_file() and path.suffix in ('.tex', '.json', '.md'):
            # Files copied here are build inputs in a private build directory,
            # not edits of their maintained public source files.
            shutil.copyfile(path, directory / path.relative_to(SOURCE))
    if digest(template) != CLASS_HASH:
        raise ValueError('The supplied template archive differs from the received original')
    with ZipFile(template) as package:
        if package.testzip() is not None:
            raise ValueError('Template archive CRC failed')
        matches = [name for name in package.namelist() if name.endswith('/bithesis.cls')]
        if len(matches) != 1:
            raise ValueError('Expected one original BIThesis class')
        class_bytes = package.read(matches[0])
    text = class_bytes.decode('utf-8')
    if '3.7.4' not in text or 'LaTeX Project Public License' not in text:
        raise ValueError('Expected the supplied LPPL BIThesis 3.7.4 class')
    if re.search(r'\\(?:write18|ShellEscape)|\\input\s*\{?\s*\|', text):
        raise ValueError('Unexpected executable template directive')
    (directory / 'bithesis.cls').write_bytes(class_bytes)
    bibliography(directory)
    facts = data_tables(directory)
    return facts, hashlib.sha256(class_bytes).hexdigest()


def compile_report(directory):
    engine = shutil.which('xelatex')
    if engine is None:
        raise RuntimeError('XeLaTeX is not installed; compilation is not complete')
    logs = []
    for pass_number in range(1, 4):
        result = subprocess.run([engine, '-no-shell-escape', '-interaction=nonstopmode',
                                 '-halt-on-error', '-file-line-error', 'main.tex'],
                                cwd=directory, capture_output=True, text=True, timeout=300,
                                env=dict(os.environ, openin_any='p', openout_any='p'))
        (directory / f'compile-pass-{pass_number}.txt').write_text(result.stdout + result.stderr)
        if result.returncode:
            print('\n'.join(result.stdout.splitlines()[-24:]))
            raise RuntimeError(f'XeLaTeX pass {pass_number} failed')
        logs.append(result.stdout)
    log = (directory / 'main.log').read_text(errors='replace')
    if re.search(r'undefined references|undefined citations|Citation .+ undefined|Reference .+ undefined', log, re.I):
        raise RuntimeError('Unresolved reference or citation remains')
    match = re.search(r'REPORT_BODY_PAGES=(\d+)', logs[-1])
    if not match:
        raise RuntimeError('Body page marker absent')
    pages = int(match.group(1))
    if pages < 30:
        raise RuntimeError(f'Only {pages} body pages; substantive report expansion is required')
    overfull = re.findall(r'Overfull \\hbox \(([^)]+)\)', log)
    return pages, overfull


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--template-zip', required=True, type=Path)
    parser.add_argument('--build-dir', required=True, type=Path)
    parser.add_argument('--prepare-only', action='store_true')
    parser.add_argument('--use-prepared', action='store_true')
    parser.add_argument('--publish', action='store_true')
    parser.add_argument('--overwrite', action='store_true')
    args = parser.parse_args()
    if args.use_prepared:
        if not (args.build_dir / 'bithesis.cls').exists():
            raise ValueError('Prepared class is absent')
        # A revised maintained source is a new build input. Private outputs are
        # inspected separately and are never substituted for source text.
        for path in SOURCE.rglob('*'):
            if path.is_file() and path.suffix in ('.tex', '.json', '.md'):
                shutil.copyfile(path, args.build_dir / path.relative_to(SOURCE))
        facts = data_tables(args.build_dir)
        bibliography(args.build_dir)
        class_hash = digest(args.build_dir / 'bithesis.cls')
    else:
        facts, class_hash = prepare(args.template_zip, args.build_dir)
    if args.prepare_only:
        print('Private build inputs prepared; no compilation or page-count acceptance claimed.')
        return
    pages, overfull = compile_report(args.build_dir)
    result = {'passed': True, 'body_pages_excluding_cover_TOC_references': pages,
              'class_version': 'BIThesis 3.7.4', 'class_sha256': class_hash,
              'template_zip_sha256': digest(args.template_zip),
              'source_figure_commands': len(re.findall(r'\\newcommand\{\\Fig', (SOURCE / 'figures/figures.tex').read_text())),
              'academic_references': 34, 'shell_escape': False,
              'overfull_hbox_messages': overfull, 'human_gold_count': facts['human_gold_count'],
              'main_graph_ready': facts['main_graph_ready'],
              'formal_model_training_completed': False, 'pdf_sha256': digest(args.build_dir / 'main.pdf')}
    (args.build_dir / 'build-report.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    if args.publish:
        pdf = SOURCE.parent / '开题报告-邱琨原-图文长版.pdf'
        bundle = SOURCE.parent / '开题报告-邱琨原-LaTeX源码.zip'
        for target in (pdf, bundle):
            if target.exists() and not args.overwrite:
                raise FileExistsError(target)
        shutil.copyfile(args.build_dir / 'main.pdf', pdf)
        with ZipFile(bundle, 'w' if args.overwrite else 'x', ZIP_DEFLATED) as package:
            for path in args.build_dir.rglob('*'):
                if path.is_file() and path.suffix in ('.tex', '.cls', '.bbl', '.dat', '.json', '.md'):
                    if path.name != 'build-report.json':
                        package.write(path, path.relative_to(args.build_dir))
        with ZipFile(bundle) as package:
            if package.testzip() is not None:
                raise RuntimeError('Generated source bundle CRC failed')
            if any(name.endswith(('.ttf', '.png', '.pdf')) or 'originality' in name or 'acknowledgements' in name for name in package.namelist()):
                raise RuntimeError('Old thesis assets appeared in the generated bundle')
        result.update(public_pdf=str(pdf.relative_to(ROOT)), public_source_bundle=str(bundle.relative_to(ROOT)),
                      source_bundle_sha256=digest(bundle))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()

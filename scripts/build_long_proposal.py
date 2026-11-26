"""Build the ACTUAL-loss illustrated master's proposal in a new private directory.

Only the byte-identical docstrip BIThesis class from the received archive is
reused. No old thesis body, fonts, signatures or raw licensed rows are imported.
The distribution is an exact allowlist, never an arbitrary build-directory glob.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from zipfile import ZipFile, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'docs/开题报告/北理工专硕/latex'
CLASS_HASH = '21ab1e018f9fcb9bf522d9d508e88b4d65697e688980ca31dfb94e8fa6cd51e6'
TARGET = 'actual-loss-four-bin-v1-2026-11-26'
OWN_INPUTS = (
    'main.tex', 'sections/01_basis.tex', 'sections/02_contents.tex',
    'sections/03_methods.tex', 'sections/04_schedule.tex',
    'sections/05_outputs.tex', 'sections/06_innovation.tex',
    'sections/07_foundation.tex', 'methods/data_events.tex',
    'methods/market_industry.tex', 'methods/graph_fusion.tex',
    'methods/experiments_system.tex', 'figures/figures.tex',
    'data/audit-summary.json', 'README.md', 'THIRD_PARTY_NOTICES.md',
)
GENERATED_INPUTS = ('bithesis.cls', 'references.bbl', 'data/yearly.dat', 'data/missing.dat')
BUNDLE_INPUTS = OWN_INPUTS + GENERATED_INPUTS


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
    facts = json.loads((SOURCE / 'data/audit-summary.json').read_text(encoding='utf-8'))
    years = facts['publication_year_rows']
    if sum(years.values()) != facts['shareholder_rows']:
        raise ValueError('Historical publication counts do not match audited raw total')
    lines = ['year wan count']
    for year, count in years.items():
        lines.append(f'{year} {count / 10000:.8f} {count}')
    (destination / 'data/yearly.dat').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    lines = ['pct field label']
    for field, label in [('InfoSource', '信息来源'), ('A_StkCd', 'A股代码'),
                         ('GDID', '股东ID'), ('PctTrdShr', '流通股比例')]:
        count = facts['shareholder_missing'][field]
        lines.append(f'{100 * count / facts["shareholder_rows"]:.8f} {label} {count:,}条')
    (destination / 'data/missing.dat').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return facts


def original_class(template):
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
    if not all(token in text for token in ('3.7.4', 'LaTeX Project Public License', 'docstrip')):
        raise ValueError('Expected the supplied docstrip LPPL BIThesis 3.7.4 class')
    if re.search(r'\\(?:write18|ShellEscape)|\\input\s*\{?\s*\|', text):
        raise ValueError('Unexpected executable template directive')
    return class_bytes


def private_directory(directory):
    if directory.is_symlink():
        raise ValueError('Build directory must not be a symlink')
    resolved = directory.resolve()
    if resolved == ROOT or ROOT in resolved.parents:
        raise ValueError('Build directory must be outside the Git repository')
    return resolved


def refresh_inputs(directory):
    for relative in OWN_INPUTS:
        path = SOURCE / relative
        if not path.is_file() or path.is_symlink():
            raise ValueError(f'Maintained input missing or symlinked: {relative}')
        target = directory / relative
        if target.is_symlink():
            raise ValueError(f'Prepared input must not be a symlink: {relative}')
        # These are private generated build inputs, never maintained-file rewrites.
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    bibliography(directory)
    return data_tables(directory)


def prepare(template, directory):
    directory = private_directory(directory)
    class_bytes = original_class(template)
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    (directory / 'bithesis.cls').write_bytes(class_bytes)
    facts = refresh_inputs(directory)
    return facts, hashlib.sha256(class_bytes).hexdigest()


def verify_bundle_inputs(directory, class_bytes):
    for relative in BUNDLE_INPUTS:
        path = directory / relative
        if not path.is_file() or path.is_symlink():
            raise ValueError(f'Bundle input missing or symlinked: {relative}')
    if (directory / 'bithesis.cls').read_bytes() != class_bytes:
        raise ValueError('Prepared class is modified; only byte-identical original may be bundled')
    for relative in OWN_INPUTS:
        if digest(directory / relative) != digest(SOURCE / relative):
            raise ValueError(f'Prepared maintained input differs: {relative}')
    # A permitted filename is not proof that its contents are aggregates.
    # Regenerate from the maintained facts/references with the same generators.
    with tempfile.TemporaryDirectory(prefix='proposal-bundle-check-') as scratch:
        expected = Path(scratch)
        (expected / 'data').mkdir()
        bibliography(expected)
        data_tables(expected)
        for relative in GENERATED_INPUTS:
            if relative != 'bithesis.cls' and digest(directory / relative) != digest(expected / relative):
                raise ValueError(f'Generated bibliography/aggregate input differs: {relative}')


def source_bundle(directory, target, template, overwrite=False):
    class_bytes = original_class(template)
    verify_bundle_inputs(directory, class_bytes)
    with ZipFile(target, 'w' if overwrite else 'x', ZIP_DEFLATED) as package:
        for relative in BUNDLE_INPUTS:
            package.write(directory / relative, relative)
    with ZipFile(target) as package:
        if package.testzip() is not None:
            raise RuntimeError('Generated source bundle CRC failed')
        if set(package.namelist()) != set(BUNDLE_INPUTS) or len(package.namelist()) != len(BUNDLE_INPUTS):
            raise RuntimeError('Source bundle does not match the approved exact allowlist')
        if package.read('bithesis.cls') != class_bytes:
            raise RuntimeError('Bundle class differs from the original dependency')


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
    if 'Missing character:' in log:
        raise RuntimeError('Missing font glyph remains in the report')
    match = re.search(r'REPORT_BODY_PAGES=(\d+)', logs[-1])
    if not match:
        raise RuntimeError('Body page marker absent')
    pages = int(match.group(1))
    if pages < 30:
        raise RuntimeError(f'Only {pages} body pages; substantive report expansion is required')
    sizes = [float(value) for value in re.findall(r'REPORT_BODY_FONT_SIZE_PT=([\d.]+)', logs[-1])]
    baselines = [float(value) for value in re.findall(r'REPORT_BODY_BASELINE_PT=([\d.]+)pt', logs[-1])]
    if len(sizes) < 8 or len(baselines) < 8:
        raise RuntimeError('Body typesetting markers absent at start or after seven chapters')
    if any(abs(value - 12 * 72.27 / 72) > .02 for value in sizes) or any(
            abs(value - 22 * 72.27 / 72) > .02 for value in baselines):
        raise RuntimeError('Body must remain 12bp/22bp, without an extra template line multiplier')
    overfull = re.findall(r'Overfull \\[hv]box \(([^)]+)\)', log)
    if overfull:
        raise RuntimeError(f'Overfull boxes remain: {overfull}')
    return pages, overfull


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--template-zip', required=True, type=Path)
    parser.add_argument('--build-dir', required=True, type=Path)
    parser.add_argument('--prepare-only', action='store_true')
    parser.add_argument('--use-prepared', action='store_true')
    parser.add_argument('--publish', action='store_true')
    parser.add_argument('--visual-reviewed', action='store_true',
                        help='Confirm inspection of rendered current sources; required for publication')
    parser.add_argument('--overwrite', action='store_true')
    args = parser.parse_args()
    directory = private_directory(args.build_dir)
    if args.prepare_only and args.publish:
        raise ValueError('Prepare-only does not publish an unaccepted report')
    if args.publish and not args.visual_reviewed:
        raise ValueError('Publication requires inspection of rendered current sources (--visual-reviewed)')
    if args.use_prepared:
        if directory.is_symlink() or not directory.is_dir():
            raise ValueError('Prepared private directory is absent or symlinked')
        expected = original_class(args.template_zip)
        class_path = directory / 'bithesis.cls'
        if class_path.is_symlink() or not class_path.is_file() or class_path.read_bytes() != expected:
            raise ValueError('Prepared class must match the unmodified received original')
        facts = refresh_inputs(directory)
        class_hash = digest(class_path)
    else:
        facts, class_hash = prepare(args.template_zip, directory)
    if args.prepare_only:
        print('Private ACTUAL-loss build inputs prepared; no compilation or page-count acceptance claimed.')
        return
    pages, overfull = compile_report(directory)
    body_text = '\n'.join((SOURCE / p).read_text(encoding='utf-8') for p in OWN_INPUTS
                          if p.endswith('.tex') and p.startswith(('sections/', 'methods/')))
    figures = (SOURCE / 'figures/figures.tex').read_text(encoding='utf-8')
    result = {'passed': True, 'target_protocol': TARGET,
              'body_pages_excluding_cover_TOC_references': pages,
              'body_CJK_characters_excluding_cover_figures_references': len(re.findall(r'[\u3400-\u9fff]', body_text)),
              'class_version': 'BIThesis 3.7.4', 'class_sha256': class_hash,
              'template_zip_sha256': digest(args.template_zip),
              'source_figure_commands': len(re.findall(r'\\newcommand\{\\Fig', figures)),
              'academic_references': 34, 'shell_escape': False,
              'overfull_box_messages': overfull, 'human_gold_count': facts['human_gold_count'],
              'main_graph_ready': facts['main_graph_ready'],
              'actual_loss_certification_completed': facts['actual_loss_certification_completed'],
              'listed_universe_certification_completed': facts['listed_universe_certification_completed'],
              'formal_features_assembled': facts['formal_features_assembled'],
              'formal_model_training_completed': False,
              'body_font_size_bp': 12, 'body_baseline_bp': 22,
              'body_typesetting_checkpoints': 8,
              'visual_review_completed': args.visual_reviewed,
              'pdf_sha256': digest(directory / 'main.pdf')}
    if args.publish:
        pdf = SOURCE.parent / '开题报告-邱琨原-图文长版.pdf'
        bundle = SOURCE.parent / '开题报告-邱琨原-LaTeX源码.zip'
        for target in (pdf, bundle):
            if target.exists() and not args.overwrite:
                raise FileExistsError(target)
            if target.is_symlink():
                raise ValueError('Publication target must not be a symlink')
        source_bundle(directory, bundle, args.template_zip, args.overwrite)
        shutil.copyfile(directory / 'main.pdf', pdf)
        result.update(public_pdf=str(pdf.relative_to(ROOT)), public_source_bundle=str(bundle.relative_to(ROOT)),
                      source_bundle_sha256=digest(bundle))
    (directory / 'build-report.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()

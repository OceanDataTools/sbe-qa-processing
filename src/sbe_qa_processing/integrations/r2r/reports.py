"""Runs the QA for R2R: the core QA with a cruise config, then R2R's tests; and writes the core
reports with R2R's certificate, plus the R2R QA 2.0 XML.
"""

from dataclasses import dataclass
from pathlib import Path

from sbe_qa_processing import reports
from sbe_qa_processing.fileset import Fileset
from sbe_qa_processing.integrations.r2r.certificate import NOTEBOOK_CELLS, certificate
from sbe_qa_processing.integrations.r2r.config import CruiseConfig, load_cruise_config
from sbe_qa_processing.integrations.r2r.rules import R2RResult, run_r2r_tests
from sbe_qa_processing.integrations.r2r.xml_report import write_qa_xml
from sbe_qa_processing.notebook import NotebookExtras
from sbe_qa_processing.qa import QAResult, run_qa
from sbe_qa_processing.readable import Readable


@dataclass
class R2RRun(Readable):
    result: QAResult  # its config is the CruiseConfig
    r2r: R2RResult
    config_path: Path | None = None

    def __str__(self) -> str:
        return "\n".join(summary(self))


def run_r2r(fileset: Fileset | Path | str, config: CruiseConfig | Path | str) -> R2RRun:
    config_path = None if isinstance(config, CruiseConfig) else Path(config)
    if config_path is not None:
        config = load_cruise_config(config_path)
    result = run_qa(fileset, config)
    return R2RRun(result, run_r2r_tests(result.fileset, result.casts, config), config_path)


def report_paths(output: Path, identifier: str) -> dict[str, Path]:
    return {"xml": output / f"{identifier}_qa.2.0.xml", **reports.report_paths(output, identifier)}


def notebook_extras(run: R2RRun, xml_name: str) -> NotebookExtras:
    """The notebook re-runs R2R's tests and writes the certificate and XML too"""
    config = run.result.config
    package = "sbe_qa_processing.integrations.r2r"
    return NotebookExtras(
        heading=f" fileset {config.fileset_id}" if config.fileset_id else "",
        reports=" and R2R QA XML",
        checks=", R2R's CTD tests",
        imports=(
            f"from {package}.certificate import certificate\n"
            f"from {package}.config import load_cruise_config\n"
            f"from {package}.rules import run_r2r_tests\n"
            f"from {package}.xml_report import write_qa_xml\n"
        ),
        config_path=run.config_path,
        load_config="load_cruise_config",
        after_run=(
            "\nr2r = run_r2r_tests(result.fileset, result.casts, result.config)\n"
            'print(f"R2R rating {r2r.rating}")'
        ),
        cells=list(NOTEBOOK_CELLS),
        write=f'print(write_qa_xml(result, r2r, OUTPUT_DIR / "{xml_name}"))\n',
        pdf_arguments=", front=certificate(result, r2r)",
    )


def write_r2r_reports(
    run: R2RRun, output: Path, execute_notebook: bool = False, theme_file: Path | None = None
) -> list[Path]:
    """The R2R QA XML, then the core reports with R2R's certificate"""
    output.mkdir(parents=True, exist_ok=True)
    xml = report_paths(output, run.result.config.identifier)["xml"]
    return [
        write_qa_xml(run.result, run.r2r, xml),
        *reports.write_reports(
            run.result,
            output,
            execute_notebook,
            pdf_front=certificate(run.result, run.r2r),
            pdf_subject=f"R2R rating {run.r2r.rating}",
            notebook_extras=notebook_extras(run, xml.name),
            theme_file=theme_file,
        ),
    ]


def summary(run: R2RRun) -> list[str]:
    """The rating and tests, then the core summary"""
    lines = [f"{run.result.config.identifier}: R2R rating {run.r2r.rating}"]
    for test in run.r2r.tests:
        failures = f"  ({', '.join(test.failures)})" if test.failures else ""
        lines.append(f"  {test.rating}  {test.name}{failures}")
    return lines + reports.summary(run.result)

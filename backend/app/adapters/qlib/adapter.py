from app.adapters.qlib.dataset_exporter import QlibDatasetExporter
from app.adapters.qlib.experiment_runner import QlibExperimentRunner
from app.adapters.qlib.prediction_importer import import_predictions


class QlibExperimentAdapter:
    """Facade that keeps Qlib exports, experiments, and imports in one adapter."""

    engine_code = "qlib"
    dataset_exporter = QlibDatasetExporter()
    experiment_runner = QlibExperimentRunner()
    prediction_importer = staticmethod(import_predictions)
    experiment_only = True
    production_enabled = False

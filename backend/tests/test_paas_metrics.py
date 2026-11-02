"""Provider contract tests: identity, pagination, units and missing observations."""

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

from vanguard.utilization.catalog import metrics_for
from vanguard.utilization.cloud import aws_samples, gcp_samples, metadata_samples
from vanguard.utilization.service import describe, query_series

NOW = datetime(2026, 11, 2, 18, 0, tzinfo=timezone.utc)


def resource(provider="gcp", kind="cloud_run_service", **extra):
    return {"id": 1, "owner_id": "o", "connection_id": "c", "provider": provider,
            "scope_id": "project-a", "region": "us-central1", "resource_type": kind,
            "resource_id": "us-central1/api", "name": "api", "attributes": {}, "last_seen_at": NOW, **extra}


def response(body):
    result = Mock()
    result.json.return_value = body
    return result


def series(name, *, region="us-central1", project="project-a", value=2):
    return {"resource": {"labels": {"service_name": name, "location": region, "project_id": project}},
            "points": [{"interval": {"endTime": NOW.isoformat()}, "value": {"doubleValue": value}}]}


class PaaSMetricsTests(unittest.TestCase):
    @patch("vanguard.utilization.cloud.gcp_credentials")
    @patch("vanguard.utilization.cloud.AuthorizedSession")
    def test_regional_names_do_not_mix_projects_regions_or_function_generations(self, factory, _credentials):
        west = resource(region="us-west1", resource_id="us-west1/api", id=2)
        function = resource(kind="cloud_function", id=3, attributes={"generation": "GEN_2", "run_service": "api"})
        session = factory.return_value.__enter__.return_value
        session.get.return_value = response({"timeSeries": [series("api", value=2), series("api", region="us-west1", value=7), series("api", project="foreign", value=99)]})
        samples, errors = gcp_samples([resource(), west, function], {}, NOW)
        self.assertFalse(errors)
        traffic = {r["id"]: value for r, metric, _, value in samples if metric.key == "requests_per_second"}
        self.assertEqual(traffic, {1: 2, 2: 7, 3: 2})
        for call in session.get.call_args_list:
            params = call.kwargs["params"]
            self.assertIn("resource.label.location", params["aggregation.groupByFields"])
            self.assertIn('resource.type = "cloud_run_revision"', params["filter"])

    @patch("vanguard.utilization.cloud.gcp_credentials")
    @patch("vanguard.utilization.cloud.AuthorizedSession")
    def test_distributions_use_p95_and_max_instead_of_summing_percentages(self, factory, _credentials):
        session = factory.return_value.__enter__.return_value
        requests = []
        def get(_url, *, params, **_):
            requests.append(params.copy())
            return response({"timeSeries": [series("api", value=.85)]})
        session.get.side_effect = get
        samples, _ = gcp_samples([resource()], {}, NOW)
        cpu = next(value for _, metric, _, value in samples if metric.key == "cpu_percent")
        self.assertEqual(cpu, 85)
        params = next(params for params in requests if "cpu/utilizations" in params["filter"])
        self.assertEqual(params["aggregation.perSeriesAligner"], "ALIGN_PERCENTILE_95")
        self.assertEqual(params["aggregation.crossSeriesReducer"], "REDUCE_MAX")
        self.assertTrue(any('response_code_class = "5xx"' in params["filter"] for params in requests))

    @patch("vanguard.utilization.cloud.gcp_credentials")
    @patch("vanguard.utilization.cloud.AuthorizedSession")
    def test_paginated_metrics_and_partial_failures_preserve_other_metrics(self, factory, _credentials):
        session = factory.return_value.__enter__.return_value
        calls = []
        def get(_url, *, params, **_):
            calls.append(params.copy())
            if "request_count" in params["filter"] and "5xx" not in params["filter"]:
                if "pageToken" not in params:
                    return response({"nextPageToken": "next"})
                return response({"timeSeries": [series("api", value=3)]})
            raise RuntimeError("denied")
        session.get.side_effect = get
        samples, errors = gcp_samples([resource()], {}, NOW)
        self.assertTrue(errors)
        self.assertTrue(any(metric.key == "requests_per_second" and value == 3 for _, metric, _, value in samples))
        self.assertTrue(any(params.get("pageToken") == "next" for params in calls))

    @patch("vanguard.utilization.cloud.boto3.Session")
    def test_rds_uses_instance_name_and_derives_storage_percentage(self, session):
        item = resource("aws", "rds_instance", name="orders", resource_id="arn:aws:rds:us-east-1:123:db:orders", attributes={"allocated_storage_gb": 100})
        requests = []
        def paginate(**kwargs):
            requests.append(kwargs)
            return [{"MetricDataResults": [{"Id": query["Id"], "StatusCode": "Complete", "Timestamps": [NOW],
                                             "Values": [10 * 1024**3 if query["MetricStat"]["Metric"]["MetricName"] == "FreeStorageSpace" else 5]}
                                            for query in kwargs["MetricDataQueries"]]}]
        session.return_value.client.return_value.get_paginator.return_value.paginate.side_effect = paginate
        samples, errors = aws_samples([item], {"access_key_id": "test", "secret_access_key": "test"}, NOW)
        self.assertFalse(errors)
        self.assertEqual(next(value for _, metric, _, value in samples if metric.key == "filesystem_percent"), 90)
        for query in requests[0]["MetricDataQueries"]:
            self.assertEqual(query["MetricStat"]["Metric"]["Dimensions"], [{"Name": "DBInstanceIdentifier", "Value": "orders"}])
        aurora = {**item, "attributes": {"engine": "aurora-postgresql", "cluster": "orders"}}
        self.assertNotIn("filesystem_percent", {m.key for m in metrics_for(aurora)})

    @patch("vanguard.utilization.cloud.boto3.Session")
    def test_daily_s3_metrics_have_storage_dimension_and_preserve_age(self, session):
        seen = NOW - timedelta(hours=30)
        item = resource("aws", "s3_bucket", name="archive", resource_id="archive")
        paginator = session.return_value.client.return_value.get_paginator.return_value.paginate
        paginator.return_value = [{"MetricDataResults": [{"Id": "m0", "StatusCode": "Complete", "Timestamps": [seen], "Values": [1024]}]}]
        samples, errors = aws_samples([item], {"access_key_id": "test", "secret_access_key": "test"}, NOW)
        self.assertFalse(errors)
        query = paginator.call_args.kwargs
        self.assertEqual((query["EndTime"] - query["StartTime"]).total_seconds(), 172800)
        self.assertEqual(query["MetricDataQueries"][0]["MetricStat"]["Period"], 86400)
        self.assertEqual(samples[0][2], seen.timestamp())
        metric = metrics_for(item)[0]
        cloud_series = [{"metric": {**{k: str(item[k]) for k in ("owner_id", "provider", "scope_id", "region", "resource_type", "resource_id")}, "__name__": "vanguard_" + metric.key}, "values": [[seen.timestamp(), "1024"]]}]
        reading = describe([item], cloud_series, now=NOW.timestamp())[0]["metrics"][0]
        self.assertEqual(reading["status"], "ok")
        self.assertEqual(reading["observed_at"], seen.isoformat())

    def test_bigquery_metadata_does_not_convert_missing_query_history_to_zero(self):
        item = resource(kind="bigquery_table", attributes={"size_bytes": 123, "rows": 0, "queries_7d": None})
        samples = metadata_samples([item])
        self.assertEqual({metric.key for _, metric, _, _ in samples}, {"storage_bytes", "rows"})
        readings = {m["key"]: m for m in describe([item], [], now=NOW.timestamp())[0]["metrics"]}
        self.assertEqual(readings["rows"]["value"], 0)
        self.assertIsNone(readings["queries_7d"]["value"])
        self.assertEqual(readings["storage_bytes"]["source"], "inventory")
        self.assertEqual(describe([item], [], now=NOW.timestamp()+1800)[0]["metrics"][0]["status"], "stale")

    def test_connections_are_engine_specific_and_gen2_without_backing_service_is_unknown(self):
        mysql = resource(kind="cloudsql_instance", attributes={"database_version": "MYSQL_8_0"})
        self.assertTrue(any(metric.cloud_name.endswith("network/connections") for metric in metrics_for(mysql)))
        self.assertEqual(metrics_for(resource(kind="cloud_function", attributes={"generation": "GEN_2"})), ())

    @patch("vanguard.utilization.service.urlopen")
    def test_metadata_history_uses_prometheus_and_falls_back_to_inventory_on_outage(self, request):
        import json

        table = resource(kind="bigquery_table", attributes={"size_bytes": 100})
        history = [{"metric": {"__name__": "vanguard_storage_bytes"}, "values": [[1, "99"]]}]
        request.return_value.__enter__.return_value.read.return_value = json.dumps({"status": "success", "data": {"resultType": "matrix", "result": history}}).encode()
        self.assertEqual(query_series([table], minutes=10080), history)
        request.assert_called_once()
        request.side_effect = OSError("offline")
        self.assertEqual(query_series([table]), [])

import unittest

from vanguard_inventory.costs.catalog import category_for
from vanguard_inventory.costs.collectors import validate_gcp_table
from vanguard_inventory.costs.schemas import CostQuery


class CostCatalogTests(unittest.TestCase):
    def test_normalizes_provider_service_names(self):
        cases = {
            "Amazon Elastic Compute Cloud - Compute": "Compute",
            "Amazon Relational Database Service": "Databases",
            "Cloud SQL": "Databases",
            "Compute Engine": "Compute",
            "Cloud Storage": "Storage",
            "Pub/Sub": "Messaging",
        }
        for service, expected in cases.items():
            with self.subTest(service=service):
                self.assertEqual(category_for(service), expected)

    def test_unknown_services_remain_visible(self):
        self.assertEqual(category_for("A newly released service"), "Other")

    def test_billing_table_identifier_is_strict(self):
        table = "billing-project.dataset.gcp_billing_export_resource_v1_ABC"
        self.assertEqual(validate_gcp_table(f"`{table}`"), table)
        with self.assertRaises(ValueError):
            validate_gcp_table("project.dataset.table`; DROP TABLE users; --")

    def test_cost_period_accepts_url_query_strings(self):
        query = CostQuery(days="30", provider="all", infrastructure="Compute")
        self.assertEqual(query.days, 30)
        self.assertEqual(query.infrastructure, "Compute")
        with self.assertRaises(ValueError):
            CostQuery(days="31", provider="all")


if __name__ == "__main__":
    unittest.main()

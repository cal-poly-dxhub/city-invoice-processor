"""Navigation group generation for UI browsing."""

from typing import Dict, List
from invoice_recon.budget_items import get_budget_item_slug, is_employee_budget_item
from invoice_recon.models import LineItem, NavigationGroup


def slugify_employee_name(first_name: str, last_name: str) -> str:
    """
    Create a slug for an employee name.

    Examples:
        ("John", "Doe") -> "john_doe"
        ("Mary Anne", "O'Brien") -> "mary_anne_o_brien"
    """
    full_name = f"{first_name} {last_name}".strip()
    # Lowercase and replace non-alphanumeric with underscore
    import re
    slug = re.sub(r"[^a-z0-9]+", "_", full_name.lower())
    # Collapse multiple underscores
    slug = re.sub(r"_+", "_", slug)
    # Strip leading/trailing underscores
    return slug.strip("_")


def build_navigation_groups(line_items: List[LineItem]) -> List[NavigationGroup]:
    """
    Build navigation groups from line items.

    Creates:
    1. One group per Budget Item
    2. Additional groups per employee for Salary/Fringe items

    Args:
        line_items: List of line items from CSV

    Returns:
        List of NavigationGroup objects
    """
    # Track groups
    budget_item_groups: Dict[str, List[str]] = {}
    employee_groups: Dict[tuple, List[str]] = {}  # (budget_item, employee_key) -> row_ids

    for item in line_items:
        budget_item = item.budget_item

        # Add to budget item group
        if budget_item not in budget_item_groups:
            budget_item_groups[budget_item] = []
        budget_item_groups[budget_item].append(item.row_id)

        # Add to employee group if applicable
        if is_employee_budget_item(budget_item):
            if item.employee_first_name and item.employee_last_name:
                employee_key = slugify_employee_name(
                    item.employee_first_name,
                    item.employee_last_name,
                )
                group_key = (budget_item, employee_key)

                if group_key not in employee_groups:
                    employee_groups[group_key] = []
                employee_groups[group_key].append(item.row_id)

    # Build NavigationGroup objects
    groups = []

    # Budget item groups
    for budget_item, row_ids in sorted(budget_item_groups.items()):
        group_id = f"bi:{get_budget_item_slug(budget_item)}"
        groups.append(
            NavigationGroup(
                group_id=group_id,
                label=budget_item,
                budget_item=budget_item,
                employee_key=None,
                line_item_ids=row_ids,
            )
        )

    # Employee groups
    for (budget_item, employee_key), row_ids in sorted(employee_groups.items()):
        # Get employee name from first item
        first_item = next(
            (item for item in line_items if item.row_id in row_ids),
            None,
        )

        if first_item:
            employee_name = (
                f"{first_item.employee_first_name} {first_item.employee_last_name}"
            ).strip()

            group_id = f"bi:{get_budget_item_slug(budget_item)}:emp:{employee_key}"
            label = f"{budget_item} — {employee_name}"

            groups.append(
                NavigationGroup(
                    group_id=group_id,
                    label=label,
                    budget_item=budget_item,
                    employee_key=employee_key,
                    line_item_ids=row_ids,
                )
            )

    return groups

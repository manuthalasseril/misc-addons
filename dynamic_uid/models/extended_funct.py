# -*- coding: utf-8 -*-

import json
from lxml import etree

from odoo.exceptions import except_orm
from odoo.models import (
    MetaModel,
    BaseModel,
    Model, TransientModel, AbstractModel,

    MAGIC_COLUMNS,
    LOG_ACCESS_COLUMNS,
)

from odoo.tools.safe_eval import safe_eval

# extra definitions for backward compatibility
browse_record_list = BaseModel
                

# Don't deal with groups, it is done by check_group().
# Need the context to evaluate the invisible attribute on tree views.
# For non-tree views, the context shouldn't be given.
def transfer_node_to_modifiers(node, modifiers, context=None, in_tree_view=False):
    if node.get('attrs'):
        if ', uid' in  node.get('attrs'):
            user_id = str(context.get('uid', 1))
            user_id = ', ' + user_id
            attrs = node.get('attrs')
            attrs = attrs.replace(', uid', user_id)
            node.set('attrs', attrs)
        modifiers.update(safe_eval(node.get('attrs')))

    if node.get('states'):
        if 'invisible' in modifiers and isinstance(modifiers['invisible'], list):
            # TODO combine with AND or OR, use implicit AND for now.
            modifiers['invisible'].append(('state', 'not in', node.get('states').split(',')))
        else:
            modifiers['invisible'] = [('state', 'not in', node.get('states').split(','))]

    for a in ('invisible', 'readonly', 'required'):
        if node.get(a):
            v = bool(safe_eval(node.get(a), {'context': context or {}}))
            if in_tree_view and a == 'invisible':
                # Invisible in a tree view has a specific meaning, make it a
                # new key in the modifiers attribute.
                modifiers['tree_invisible'] = v
            elif v or (a not in modifiers or not isinstance(modifiers[a], list)):
                # Don't set the attribute to False if a dynamic value was
                # provided (i.e. a domain from attrs or states).
                modifiers[a] = v
                
                
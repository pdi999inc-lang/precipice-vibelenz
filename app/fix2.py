
import re
c = open('behavior.py').read()
# Remove the orphaned indented block outside the class
c = re.sub(r'\n    def to_schema_dict\(self\):.*?return flags\n', '', c, flags=re.DOTALL)
open('behavior.py','w').write(c)
print('removed orphan')


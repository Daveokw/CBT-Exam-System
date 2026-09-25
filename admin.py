import warnings
warnings.filterwarnings('ignore', category=UserWarning, module='pandas')
import streamlit as st
from db import get_db_connection
import pandas as pd
import numpy as np
from fpdf import FPDF
import json
import plotly.express as px
import plotly.graph_objects as go
import io
import hashlib
import re
import os
import uuid
import sqlite3
from PIL import Image, ImageOps, UnidentifiedImageError
from ai import (
    AIUnavailable,
    ai_available,
    classify_question,
    classify_questions,
    format_questions,
    summarise_class_results,
)

IMAGE_SUFFIX_FORMATS = {
    ".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG",
    ".gif": "GIF", ".webp": "WEBP", ".bmp": "BMP",
    ".tif": "TIFF", ".tiff": "TIFF",
}
MAX_IMAGE_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_IMAGE_PIXELS = 12_000_000
MAX_STORED_IMAGE_BYTES = 10 * 1024 * 1024


def prepare_question_image(image_bytes, filename):
    """Validate and normalise an uploaded question image; discard animation and metadata."""
    suffix = os.path.splitext(filename)[1].lower()
    if suffix not in IMAGE_SUFFIX_FORMATS:
        raise ValueError("Upload a PNG, JPEG, GIF, WebP, BMP or TIFF image.")
    if not image_bytes or len(image_bytes) > MAX_IMAGE_UPLOAD_BYTES:
        raise ValueError("The image must be non-empty and no larger than 5 MB.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(image_bytes)) as source:
                if source.format != IMAGE_SUFFIX_FORMATS[suffix]:
                    raise ValueError("The filename extension does not match the image format.")
                if source.width * source.height > MAX_IMAGE_PIXELS:
                    raise ValueError("The image is too large; use at most 12 million pixels.")
                source.seek(0)
                image = ImageOps.exif_transpose(source)
                image.load()
                has_alpha = "A" in image.getbands() or "transparency" in image.info
                image = image.convert("RGBA" if has_alpha else "RGB")
                image.thumbnail((2400, 2400), Image.Resampling.LANCZOS)
                output = io.BytesIO()
                output_suffix = ".png" if has_alpha or source.format in {"PNG", "GIF", "BMP", "TIFF"} else ".jpg"
                if output_suffix == ".png":
                    image.save(output, format="PNG", compress_level=3)
                else:
                    image.save(output, format="JPEG", quality=88)
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ValueError("The selected file is not a valid or safe image.") from exc
    prepared = output.getvalue()
    if len(prepared) > MAX_STORED_IMAGE_BYTES:
        raise ValueError("The processed image is too large; choose a smaller image.")
    return prepared, output_suffix

def admin_dashboard():
    st.title("Admin Dashboard")

    menu = ["Create Test", "Add Questions", "View Tests", "Student Search", "Overall Analytics"]
    choice = st.sidebar.radio("Navigation", menu)

    if choice == "Create Test":
        create_test()
    elif choice == "Add Questions":
        add_questions()
    elif choice == "View Tests":
        view_tests()
    elif choice == "Student Search":
        student_search_analytics()
    elif choice == "Overall Analytics":
        test_analytics()

# Local fallback classifier used when AI is unavailable.
def auto_classify_topic(question_text):
    """
    Classifies a question into an academic topic using a broad, score-based keyword
    matching approach. Every category accumulates a score from keyword hits, and the
    category with the highest total score wins. This is far more accurate than a
    first-match approach and handles synonyms and overlapping language gracefully.
    """
    text = f" {re.sub(r'[^a-z0-9]+', ' ', question_text.lower()).strip()} "

    # ----------------------------------------------------------------
    # CATEGORY DEFINITIONS
    # Each entry is (category_name, [list of keywords/phrases]).
    # Longer, more specific phrases score the same as single words, so
    # we weight them by length to reward specificity.
    # ----------------------------------------------------------------
    categories = {
        # --- Mathematics ---
        "Mathematics": [
            'algebra', 'calculus', 'equation', 'equations', 'derivative', 'differentiate',
            'differentiation', 'integral', 'integration', 'matrix', 'matrices', 'polynomial',
            'theorem', 'evaluate', 'solve for', 'function', 'logarithm', 'logarithmic',
            'exponential', 'arithmetic', 'geometric series', 'sequence', 'progression',
            'quadratic', 'linear equation', 'simultaneous', 'factorial', 'permutation',
            'combination', 'binomial', 'trigonometry', 'sine', 'cosine', 'tangent',
            'pythagoras', 'set theory', 'modular arithmetic', 'number theory', 'prime number',
            'fraction', 'ratio', 'proportion', 'percentage', 'decimal', 'surd',
        ],

        # --- Statistics & Probability ---
        "Statistics & Probability": [
            'statistics', 'probability', 'mean', 'median', 'mode', 'variance',
            'standard deviation', 'distribution', 'normal distribution', 'hypothesis',
            'regression', 'correlation', 'chi-square', 'confidence interval', 'sample',
            'population', 'random variable', 'expected value', 'bayes', 'frequency',
            'histogram', 'quartile', 'interquartile', 'skewness', 'kurtosis', 'p-value',
        ],

        # --- Geometry ---
        "Geometry": [
            'geometry', 'angle', 'triangle', 'circle', 'radius', 'diameter', 'circumference',
            'area', 'volume', 'perimeter', 'polygon', 'rectangle', 'square', 'parallelogram',
            'trapezium', 'rhombus', 'ellipse', 'cone', 'cylinder', 'sphere', 'cube',
            'cuboid', 'coordinate geometry', 'locus', 'vector', 'bearing', 'transformation',
            'symmetry', 'congruent', 'similar triangles', 'proof',
        ],

        # --- Physics ---
        "Physics": [
            'velocity', 'acceleration', 'force', 'gravity', 'gravitational', 'quantum',
            'thermodynamics', 'momentum', 'joule', 'newton', 'kinetic energy',
            'potential energy', 'magnetic', 'friction', 'wave', 'wavelength', 'frequency',
            'amplitude', 'refraction', 'reflection', 'optics', 'lens', 'mirror',
            'electric field', 'current', 'voltage', 'resistance', 'capacitor', 'inductor',
            'circuit', 'ohm', 'watt', 'power', 'work done', 'pressure', 'density',
            'buoyancy', 'fluid mechanics', 'relativity', 'nuclear', 'radioactive',
            'half life', 'fission', 'fusion', 'electromagnetic', 'photon', 'electron',
            'proton', 'neutron', 'atom', 'energy level', 'speed of light',
        ],

        # --- Chemistry ---
        "Chemistry": [
            'molecule', 'compound', 'reaction', 'acid', 'base', 'alkali', 'chemistry',
            'periodic table', 'covalent', 'ionic', 'isotope', 'molar', 'mole',
            'molarity', 'concentration', 'titration', 'oxidation', 'reduction',
            'redox', 'electrolysis', 'electrode', 'catalyst', 'equilibrium',
            'enthalpy', 'entropy', 'bond', 'bonding', 'organic chemistry',
            'hydrocarbon', 'alkane', 'alkene', 'benzene', 'polymer', 'monomer',
            'halogen', 'noble gas', 'transition metal', 'electron configuration',
            'valence', 'stoichiometry', 'empirical formula', 'molecular formula',
            'ph scale', 'buffer', 'indicator', 'salt', 'hydrolysis', 'fermentation',
        ],

        # --- Biology & Life Sciences ---
        "Biology": [
            'cell', 'dna', 'rna', 'mitochondria', 'organism', 'anatomy', 'biology',
            'protein', 'enzyme', 'species', 'genetics', 'respiration', 'photosynthesis',
            'chromosome', 'gene', 'allele', 'dominant', 'recessive', 'mutation',
            'evolution', 'natural selection', 'adaptation', 'ecosystem', 'food chain',
            'food web', 'biome', 'habitat', 'population', 'biodiversity', 'taxonomy',
            'kingdom', 'phylum', 'class', 'order', 'family', 'genus', 'osmosis',
            'diffusion', 'active transport', 'membrane', 'nucleus', 'ribosome',
            'mitosis', 'meiosis', 'fertilisation', 'embryo', 'immune system',
            'antibody', 'antigen', 'vaccine', 'bacteria', 'virus', 'fungi', 'parasite',
            'digestion', 'excretion', 'nervous system', 'hormone', 'endocrine',
            # High-weight multi-word phrases to disambiguate from Physics
            'powerhouse of the cell', 'cell organelle', 'cell biology',
            'cell membrane', 'cell wall', 'cell division', 'cell respiration',
            'living organism', 'living things', 'plant cell', 'animal cell',
        ],

        # --- Human Anatomy & Physiology ---
        "Anatomy & Physiology": [
            'anatomy', 'physiology', 'organ', 'tissue', 'bone', 'muscle', 'skeleton',
            'heart', 'lung', 'liver', 'kidney', 'brain', 'spinal cord', 'artery',
            'vein', 'capillary', 'blood', 'plasma', 'red blood cell', 'white blood cell',
            'haemoglobin', 'circulation', 'respiratory', 'digestive system',
            'reproductive system', 'urinary system', 'lymphatic', 'neuron',
            'synapse', 'reflex', 'homeostasis', 'metabolism',
        ],

        # --- Computer Science & Programming ---
        "Programming & Computer Science": [
            'python', 'java', 'c++', 'javascript', 'loop', 'variable', 'syntax',
            'compiler', 'interpreter', 'algorithm', 'boolean', 'array', 'string',
            'function', 'class', 'object', 'inheritance', 'polymorphism',
            'encapsulation', 'recursion', 'data structure', 'linked list', 'stack',
            'queue', 'tree', 'graph', 'sorting', 'searching', 'binary search',
            'time complexity', 'big o', 'pseudocode', 'flowchart', 'debugging',
            'version control', 'git', 'api', 'library', 'framework', 'ide',
            'source code', 'runtime', 'exception', 'pointer', 'memory allocation',
        ],

        # --- Databases & Data Science ---
        "Databases & Data Science": [
            'sql', 'database', 'mysql', 'postgresql', 'oracle', 'table', 'query',
            'record', 'rdbms', 'nosql', 'mongodb', 'dataframe', 'pandas', 'dataset',
            'primary key', 'foreign key', 'join', 'normalisation', 'entity',
            'relationship', 'data warehouse', 'etl', 'schema', 'index', 'transaction',
            'acid', 'data mining', 'big data', 'hadoop', 'spark',
        ],

        # --- Networking & Cloud Computing ---
        "Networking & Cloud Computing": [
            'network', 'ip address', 'router', 'switch', 'protocol', 'tcp', 'udp',
            'internet', 'cloud', 'aws', 'azure', 'google cloud', 'bandwidth', 'lan',
            'wan', 'subnet', 'dns', 'dhcp', 'http', 'https', 'ftp', 'firewall',
            'osi model', 'packet', 'topology', 'ethernet', 'wi-fi', 'vpn',
            'proxy', 'server', 'client', 'port', 'socket', 'latency',
        ],

        # --- Computer Hardware & Operating Systems ---
        "Computer Hardware & Operating Systems": [
            'hardware', 'cpu', 'ram', 'motherboard', 'memory', 'hard disk', 'ssd',
            'gpu', 'peripheral', 'operating system', 'linux', 'windows', 'unix',
            'bios', 'boot', 'process', 'thread', 'scheduling', 'deadlock',
            'virtual memory', 'cache', 'register', 'input', 'output', 'device driver',
            'file system', 'kernel', 'shell',
        ],

        # --- Cybersecurity ---
        "Cybersecurity": [
            'cybersecurity', 'security', 'password', 'encryption', 'decryption',
            'hacking', 'firewall', 'malware', 'phishing', 'virus', 'trojan',
            'ransomware', 'spyware', 'cryptography', 'public key', 'private key',
            'ssl', 'tls', 'authentication', 'authorisation', 'access control',
            'vulnerability', 'exploit', 'intrusion', 'forensics', 'penetration testing',
        ],

        # --- Artificial Intelligence & Machine Learning ---
        "Artificial Intelligence": [
            'artificial intelligence', 'machine learning', 'neural network', 'deep learning',
            'natural language processing', 'nlp', 'training data', 'model',
            'classification', 'regression', 'clustering', 'supervised learning',
            'unsupervised learning', 'reinforcement learning', 'decision tree',
            'random forest', 'support vector machine', 'svm', 'gradient descent',
            'backpropagation', 'convolutional', 'generative', 'transformer',
            'chatgpt', 'large language model', 'bias', 'overfitting',
        ],

        # --- Web Development ---
        "Web Development": [
            'html', 'css', 'javascript', 'web design', 'browser', 'frontend',
            'backend', 'react', 'angular', 'vue', 'node', 'dom', 'responsive design',
            'bootstrap', 'rest api', 'json', 'xml', 'http request', 'web server',
            'wordpress', 'hosting', 'deployment', 'webpack', 'typescript',
        ],

        # --- Business & Economics ---
        "Business & Economics": [
            'market', 'supply', 'demand', 'revenue', 'profit', 'loss', 'economy',
            'inflation', 'stock', 'accounting', 'gdp', 'monopoly', 'oligopoly',
            'elasticity', 'fiscal policy', 'monetary policy', 'interest rate',
            'investment', 'entrepreneur', 'business plan', 'marketing', 'branding',
            'management', 'organisation', 'human resources', 'balance sheet',
            'income statement', 'cash flow', 'depreciation', 'assets', 'liabilities',
            'shareholders', 'dividend', 'budget', 'taxation', 'trade', 'export',
            'import', 'tariff', 'globalisation',
        ],

        # --- Law & Ethics ---
        "Law & Ethics": [
            'court', 'plaintiff', 'defendant', 'contract', 'liability', 'jurisdiction',
            'statute', 'constitution', 'legal', 'criminal', 'litigation', 'tort',
            'negligence', 'damages', 'remedy', 'appeal', 'precedent', 'common law',
            'civil law', 'solicitor', 'barrister', 'judiciary', 'legislation',
            'parliament', 'rights', 'obligation', 'ethics', 'morality', 'consent',
            'property law', 'estate', 'tenant', 'landlord', 'deed', 'mortgage',
            'lease', 'ownership', 'easement', 'zoning', 'conveyance', 'intellectual property',
        ],

        # --- History ---
        "History": [
            'history', 'war', 'century', 'empire', 'revolution', 'colonial',
            'colonialism', 'independence', 'civilisation', 'ancient', 'medieval',
            'renaissance', 'industrial revolution', 'world war', 'cold war',
            'slavery', 'abolition', 'monarchy', 'republic', 'dynasty', 'treaty',
            'propaganda', 'nationalism', 'imperialism', 'apartheid', 'civil rights',
            'nigerian history', 'biafra', 'pre-colonial', 'nigeria', 'nigerian', 'president of nigeria',
        ],

        # --- Geography ---
        "Geography": [
            'geography', 'continent', 'ocean', 'capital city', 'country', 'latitude',
            'longitude', 'climate', 'vegetation', 'rainfall', 'temperature', 'erosion',
            'deposition', 'river', 'mountain', 'plateau', 'valley', 'delta',
            'population density', 'urbanisation', 'migration', 'settlement',
            'natural resource', 'mineral', 'petroleum', 'savanna', 'rainforest',
            'desert', 'tundra', 'nigeria', 'africa', 'west africa',
        ],

        # --- Government & Political Science ---
        "Government & Political Science": [
            'government', 'democracy', 'politics', 'parliament', 'senate', 'election',
            'constitution', 'federal', 'state', 'local government', 'legislature',
            'executive', 'judiciary', 'separation of powers', 'sovereignty',
            'political party', 'voting', 'referendum', 'policy', 'governance',
            'republic', 'monarchy', 'autocracy', 'dictatorship', 'president',
            'prime minister', 'cabinet', 'bureaucracy', 'diplomacy', 'international relations',
            'united nations', 'human rights', 'citizenship',
        ],

        # --- English Language & Literature ---
        "English Language & Literature": [
            'grammar', 'syntax', 'vocabulary', 'noun', 'pronoun', 'verb', 'adjective',
            'adverb', 'preposition', 'conjunction', 'tense', 'passive voice',
            'active voice', 'sentence', 'clause', 'phrase', 'punctuation',
            'comprehension', 'essay', 'prose', 'poetry', 'poem', 'stanza', 'verse',
            'novel', 'author', 'character', 'plot', 'theme', 'setting', 'metaphor',
            'simile', 'alliteration', 'irony', 'symbolism', 'figurative language',
            'diction', 'tone', 'narrator', 'literary device',
        ],

        # --- Sociology & Social Studies ---
        "Sociology & Social Studies": [
            'society', 'culture', 'social', 'community', 'family', 'socialisation',
            'social class', 'stratification', 'inequality', 'gender', 'race',
            'ethnicity', 'religion', 'norm', 'value', 'institution', 'deviance',
            'crime', 'poverty', 'urbanisation', 'globalisation', 'social change',
            'identity', 'prejudice', 'discrimination', 'social mobility',
        ],

        # --- Psychology ---
        "Psychology": [
            'psychology', 'behaviour', 'cognition', 'perception', 'memory', 'learning',
            'motivation', 'emotion', 'personality', 'development', 'freud', 'piaget',
            'pavlov', 'skinner', 'conditioning', 'reinforcement', 'stimulus', 'response',
            'mental health', 'disorder', 'therapy', 'counselling', 'intelligence',
            'iq', 'attitude', 'social influence', 'obedience', 'conformity',
        ],

        # --- Medicine & Health Sciences ---
        "Medicine & Health": [
            'disease', 'diagnosis', 'treatment', 'medication', 'drug', 'dose',
            'symptom', 'syndrome', 'infection', 'inflammation', 'surgery',
            'clinical', 'patient', 'health', 'medicine', 'pharmacy', 'pathology',
            'epidemiology', 'public health', 'nutrition', 'diet', 'vitamin',
            'mineral', 'calorie', 'obesity', 'diabetes', 'hypertension', 'cancer',
            'malaria', 'hiv', 'aids', 'tuberculosis', 'immunisation', 'first aid',
        ],

        # --- Engineering ---
        "Engineering": [
            'engineering', 'civil engineering', 'mechanical', 'electrical engineering',
            'structural', 'load', 'stress', 'strain', 'beam', 'column', 'truss',
            'material', 'alloy', 'tensile strength', 'compressive', 'welding',
            'thermodynamic cycle', 'heat transfer', 'fluid flow', 'hydraulics',
            'pneumatics', 'gear', 'shaft', 'turbine', 'engine', 'automation',
            'control system', 'signal', 'digital logic', 'sensor', 'actuator',
        ],

        # --- Agriculture & Environmental Science ---
        "Agriculture & Environmental Science": [
            'agriculture', 'farming', 'crop', 'soil', 'fertiliser', 'irrigation',
            'pest', 'pesticide', 'livestock', 'poultry', 'fishery', 'forestry',
            'deforestation', 'afforestation', 'erosion', 'land use', 'food production',
            'climate change', 'global warming', 'greenhouse gas', 'carbon dioxide',
            'pollution', 'waste management', 'recycling', 'biodiversity', 'conservation',
            'renewable energy', 'solar', 'wind energy', 'fossil fuel',
        ],
    }

    # ----------------------------------------------------------------
    # SCORING: count keyword hits per category, weight multi-word phrases
    # ----------------------------------------------------------------
    scores = {}
    for category, keywords in categories.items():
        score = 0
        for kw in keywords:
            term = re.sub(r'[^a-z0-9]+', ' ', kw.lower()).strip()
            if f" {term} " in text:
                # Longer phrases carry more weight (more specific)
                score += len(kw.split())
        scores[category] = score

    best_category = max(scores, key=lambda c: scores[c])

    if scores[best_category] == 0:
        return "General Knowledge"

    return best_category


# ---------------------------------------------------------------------------
# SUB-TOPIC CLASSIFIER
# Maps each broad topic to specific sub-topics and their trigger keywords.
# Used to pinpoint exactly what a student needs to revise in their report.
# ---------------------------------------------------------------------------
SUBTOPIC_MAP = {
    "Mathematics": {
        "Algebra": ["algebra", "algebraic", "expression", "factorisation", "factorize", "expand brackets"],
        "Calculus": ["calculus", "derivative", "differentiate", "differentiation", "integral", "integration", "limit"],
        "Trigonometry": ["trigonometry", "sine", "cosine", "tangent", "sin", "cos", "tan", "pythagoras", "hypotenuse"],
        "Number Theory": ["prime number", "prime", "factor", "divisor", "lcm", "hcf", "modular", "modulo", "number theory"],
        "Matrices": ["matrix", "matrices", "determinant", "eigenvalue", "transpose"],
        "Sequences & Series": ["sequence", "series", "arithmetic progression", "geometric progression", "geometric series", "fibonacci"],
        "Combinatorics": ["permutation", "combination", "factorial", "binomial", "counting"],
        "Linear Equations": ["linear equation", "simultaneous", "solve for", "quadratic", "inequality"],
        "Logarithms & Exponentials": ["logarithm", "logarithmic", "exponential", "log", "ln", "natural log"],
    },
    "Statistics & Probability": {
        "Probability Theory": ["probability", "chance", "event", "sample space", "bayes", "random variable", "expected value"],
        "Descriptive Statistics": ["mean", "median", "mode", "variance", "standard deviation", "range", "quartile", "interquartile"],
        "Hypothesis Testing": ["hypothesis", "null hypothesis", "p-value", "significance", "confidence interval", "t-test", "chi-square"],
        "Regression & Correlation": ["regression", "correlation", "scatter", "line of best fit"],
        "Distributions": ["distribution", "normal distribution", "binomial distribution", "poisson", "skewness", "kurtosis"],
        "Data Analysis": ["histogram", "frequency", "cumulative", "class interval", "ogive", "data collection"],
    },
    "Geometry": {
        "Plane Geometry": ["angle", "triangle", "polygon", "parallelogram", "rhombus", "trapezium", "perimeter", "area"],
        "Solid Geometry": ["volume", "cube", "cuboid", "sphere", "cone", "cylinder", "prism", "surface area"],
        "Circle Theorems": ["circle", "radius", "diameter", "circumference", "arc", "chord", "sector"],
        "Coordinate Geometry": ["coordinate", "locus", "gradient", "midpoint", "distance formula", "cartesian"],
        "Vectors & Transformations": ["vector", "transformation", "rotation", "reflection", "translation", "enlargement", "symmetry"],
    },
    "Physics": {
        "Mechanics": ["velocity", "acceleration", "force", "momentum", "friction", "gravity", "gravitational", "newton", "projectile", "work done", "kinetic energy", "potential energy"],
        "Thermodynamics": ["thermodynamics", "temperature", "heat", "entropy", "thermal", "specific heat", "latent heat"],
        "Electromagnetism": ["electric field", "magnetic", "current", "voltage", "resistance", "capacitor", "inductor", "circuit", "ohm", "watt", "electromagnetic"],
        "Optics & Waves": ["wave", "wavelength", "frequency", "amplitude", "refraction", "reflection", "optics", "lens", "mirror", "diffraction"],
        "Nuclear Physics": ["nuclear", "radioactive", "half life", "fission", "fusion", "radiation", "alpha", "beta", "gamma"],
        "Fluid Mechanics": ["pressure", "density", "buoyancy", "fluid mechanics", "pascal", "archimedes"],
        "Quantum Physics": ["quantum", "photon", "energy level", "atomic model", "wave-particle"],
    },
    "Chemistry": {
        "Organic Chemistry": ["organic chemistry", "hydrocarbon", "alkane", "alkene", "alkyne", "benzene", "polymer", "monomer", "ester", "alcohol", "carboxylic"],
        "Acids & Bases": ["acid", "base", "alkali", "ph scale", "neutralisation", "buffer", "indicator", "titration"],
        "Chemical Bonding": ["covalent", "ionic", "bond", "bonding", "valence", "electron configuration", "electronegativity"],
        "Electrochemistry": ["electrolysis", "electrode", "anode", "cathode", "oxidation", "reduction", "redox", "electrolyte"],
        "Stoichiometry": ["mole", "molar", "molarity", "stoichiometry", "empirical formula", "molecular formula", "concentration"],
        "Thermochemistry": ["enthalpy", "entropy", "exothermic", "endothermic", "activation energy", "catalyst", "equilibrium"],
        "Periodic Table": ["periodic table", "group", "period", "halogen", "noble gas", "transition metal", "isotope", "atomic number", "atomic mass"],
    },
    "Biology": {
        "Cell Biology": ["cell", "mitochondria", "nucleus", "ribosome", "membrane", "cell wall", "organelle", "cytoplasm", "cell division", "plant cell", "animal cell"],
        "Genetics & Heredity": ["dna", "rna", "gene", "genetics", "chromosome", "allele", "dominant", "recessive", "mutation", "genotype", "phenotype"],
        "Evolution & Natural Selection": ["evolution", "natural selection", "adaptation", "darwin", "survival", "speciation", "fossil"],
        "Ecology": ["ecosystem", "food chain", "food web", "biome", "habitat", "biodiversity", "producer", "consumer", "decomposer"],
        "Physiology": ["respiration", "photosynthesis", "digestion", "excretion", "hormone", "nervous system", "endocrine", "osmosis", "diffusion"],
        "Microbiology": ["bacteria", "virus", "fungi", "parasite", "infection", "immune system", "antibody", "antigen", "vaccine"],
        "Reproduction": ["mitosis", "meiosis", "fertilisation", "embryo", "gamete", "sexual reproduction", "asexual reproduction"],
    },
    "Anatomy & Physiology": {
        "Cardiovascular System": ["heart", "artery", "vein", "capillary", "blood", "plasma", "red blood cell", "white blood cell", "haemoglobin", "circulation", "blood pressure"],
        "Respiratory System": ["lung", "respiratory", "breathing", "oxygen", "carbon dioxide", "alveoli", "diaphragm", "trachea"],
        "Nervous System": ["neuron", "brain", "spinal cord", "synapse", "reflex", "nerve", "dendrite", "axon"],
        "Digestive System": ["digestive system", "stomach", "intestine", "liver", "pancreas", "enzyme", "absorption"],
        "Musculoskeletal System": ["bone", "muscle", "skeleton", "joint", "cartilage", "ligament", "tendon"],
        "Endocrine System": ["hormone", "endocrine", "gland", "insulin", "adrenaline", "thyroid", "pituitary", "homeostasis"],
        "Urinary & Reproductive System": ["kidney", "urinary", "reproductive system", "ovary", "testes", "uterus", "nephron"],
    },
    "Programming & Computer Science": {
        "OOP": ["class", "object", "inheritance", "polymorphism", "encapsulation", "abstraction", "constructor", "method", "instance"],
        "Data Structures": ["linked list", "stack", "queue", "tree", "graph", "heap", "hash table", "data structure"],
        "Algorithms": ["algorithm", "sorting", "searching", "binary search", "merge sort", "quick sort", "time complexity", "big o"],
        "Variables & Data Types": ["variable", "data type", "integer", "string", "float", "boolean", "char", "null"],
        "Control Flow": ["loop", "for loop", "while loop", "if statement", "conditional", "switch", "iteration"],
        "Functions & Recursion": ["function", "recursion", "recursive", "parameter", "argument", "return", "scope"],
        "Memory Management": ["pointer", "memory allocation", "garbage collection", "reference", "heap memory", "stack memory"],
        "Version Control": ["git", "version control", "commit", "branch", "merge", "repository"],
    },
    "Databases & Data Science": {
        "SQL Queries": ["sql", "select", "where", "group by", "order by", "having", "join", "inner join", "left join", "query"],
        "Normalisation": ["normalisation", "normal form", "1nf", "2nf", "3nf", "bcnf", "functional dependency", "redundancy"],
        "Keys & Relationships": ["primary key", "foreign key", "unique key", "candidate key", "relationship", "entity", "one to many"],
        "Database Design": ["schema", "erd", "entity relationship", "database design", "table", "rdbms"],
        "Transactions & ACID": ["transaction", "acid", "atomicity", "consistency", "isolation", "durability", "rollback", "commit"],
        "Data Warehousing": ["data warehouse", "etl", "olap", "data mining", "big data", "hadoop", "spark"],
        "NoSQL": ["nosql", "mongodb", "document store", "key-value", "column store", "graph database"],
    },
    "Networking & Cloud Computing": {
        "IP Addressing": ["ip address", "ipv4", "ipv6", "ip class", "class a", "class b", "class c", "loopback", "broadcast address"],
        "Subnetting": ["subnet", "subnet mask", "subnetting", "cidr", "netmask", "network address", "host address"],
        "DNS & DHCP": ["dns", "domain name", "dhcp", "domain name system", "name resolution", "hostname"],
        "OSI Model": ["osi model", "osi layer", "application layer", "transport layer", "network layer", "data link layer", "physical layer", "presentation layer", "session layer"],
        "Routing & Switching": ["router", "routing", "routing protocol", "rip", "ospf", "bgp", "switch", "forwarding"],
        "Network Protocols": ["tcp", "udp", "ftp", "http", "https", "smtp", "protocol", "packet", "port", "socket"],
        "Cloud Services": ["aws", "azure", "google cloud", "saas", "paas", "iaas", "cloud computing", "virtual machine", "container"],
        "Network Topology": ["topology", "star topology", "bus topology", "ring topology", "mesh topology", "lan", "wan", "vpn"],
    },
    "Computer Hardware & Operating Systems": {
        "CPU Architecture": ["cpu", "processor", "core", "clock speed", "register", "alu", "control unit", "instruction cycle"],
        "Memory Management": ["ram", "virtual memory", "cache", "memory hierarchy", "paging", "segmentation"],
        "File Systems": ["file system", "fat", "ntfs", "ext", "directory", "file path", "file permission"],
        "Process Scheduling": ["process", "thread", "scheduling", "deadlock", "round robin", "context switch", "semaphore"],
        "I/O Management": ["input", "output", "peripheral", "device driver", "interrupt", "dma", "bus", "usb"],
        "BIOS & Boot": ["bios", "uefi", "boot", "bootloader", "post", "firmware"],
        "OS Concepts": ["operating system", "linux", "windows", "unix", "kernel", "shell", "system call"],
    },
    "Cybersecurity": {
        "Encryption & Cryptography": ["encryption", "decryption", "cryptography", "cipher", "aes", "rsa", "public key", "private key", "hash", "sha", "md5"],
        "Authentication & Access Control": ["authentication", "authorisation", "access control", "password", "two factor", "mfa", "biometric", "certificate", "ssl", "tls"],
        "Malware & Threats": ["malware", "virus", "trojan", "ransomware", "spyware", "worm", "phishing", "social engineering", "dos", "ddos"],
        "Network Security": ["firewall", "intrusion detection", "ids", "ips", "packet filtering", "network monitoring"],
        "Penetration Testing": ["penetration testing", "ethical hacking", "vulnerability", "exploit", "reconnaissance", "payload"],
        "Digital Forensics": ["forensics", "digital evidence", "incident response", "log analysis", "chain of custody"],
    },
    "Artificial Intelligence": {
        "Machine Learning": ["machine learning", "supervised learning", "unsupervised learning", "training data", "classification", "clustering", "overfitting"],
        "Deep Learning": ["deep learning", "neural network", "convolutional", "cnn", "rnn", "lstm", "backpropagation", "activation function"],
        "Natural Language Processing": ["natural language processing", "nlp", "tokenisation", "stemming", "lemmatisation", "sentiment analysis"],
        "Reinforcement Learning": ["reinforcement learning", "reward", "agent", "environment", "policy", "q-learning"],
        "AI Concepts": ["artificial intelligence", "turing test", "expert system", "large language model", "transformer", "chatgpt"],
        "Data Preprocessing": ["feature engineering", "data cleaning", "training set", "validation set", "cross validation"],
    },
    "Web Development": {
        "HTML & CSS": ["html", "css", "tag", "element", "attribute", "stylesheet", "selector", "flexbox", "grid", "bootstrap"],
        "JavaScript": ["javascript", "dom", "event listener", "async", "promise", "callback", "arrow function", "es6"],
        "Frontend Frameworks": ["react", "angular", "vue", "typescript", "webpack", "component", "state management"],
        "Backend Development": ["backend", "server side", "node", "express", "django", "flask", "php", "middleware"],
        "REST APIs": ["rest api", "api", "endpoint", "http request", "get", "post", "put", "delete", "json", "xml", "restful"],
        "Responsive Design": ["responsive design", "mobile first", "media query", "viewport", "breakpoint"],
        "Web Security": ["xss", "csrf", "sql injection", "cors", "web security", "cookie", "session"],
    },
    "Business & Economics": {
        "Microeconomics": ["supply", "demand", "elasticity", "market structure", "monopoly", "oligopoly", "perfect competition"],
        "Macroeconomics": ["gdp", "inflation", "unemployment", "fiscal policy", "monetary policy", "interest rate", "economic growth"],
        "Accounting": ["balance sheet", "income statement", "cash flow", "depreciation", "assets", "liabilities", "equity", "debit", "credit"],
        "Marketing": ["marketing", "branding", "market research", "advertising", "promotion"],
        "Management": ["management", "leadership", "organisation", "human resources", "motivation", "planning"],
        "Finance & Investment": ["investment", "stock", "dividend", "bond", "portfolio", "risk", "return", "capital", "budget"],
    },
    "Law & Ethics": {
        "Contract Law": ["contract", "offer", "acceptance", "consideration", "breach", "remedy", "void", "voidable"],
        "Criminal Law": ["criminal", "crime", "prosecution", "defendant", "guilty", "sentence", "offence", "mens rea", "actus reus"],
        "Property Law": ["property law", "land", "estate", "tenant", "landlord", "deed", "mortgage", "lease", "ownership"],
        "Intellectual Property": ["intellectual property", "copyright", "trademark", "patent", "trade secret", "infringement"],
        "Ethics & Morality": ["ethics", "morality", "moral", "ethical", "consent", "autonomy", "justice", "rights"],
        "Constitutional Law": ["constitution", "constitutional", "bill of rights", "separation of powers", "sovereignty", "federalism"],
        "Tort Law": ["tort", "negligence", "duty of care", "damages", "liability", "nuisance", "defamation"],
    },
    "History": {
        "World Wars": ["world war", "first world war", "second world war", "ww1", "ww2", "trench warfare", "holocaust", "atomic bomb"],
        "Colonial History": ["colonialism", "colonisation", "colonial", "imperialism", "empire", "independence", "slave trade", "decolonisation"],
        "Nigerian History": ["nigeria", "nigerian", "biafra", "lugard", "amalgamation", "pre-colonial", "nnamdi azikiwe", "independence 1960"],
        "Ancient Civilisations": ["ancient", "egypt", "rome", "greece", "mesopotamia", "civilisation", "pharaoh", "pyramid"],
        "Modern History": ["cold war", "communism", "capitalism", "revolution", "civil rights", "apartheid", "nationalism"],
    },
    "Geography": {
        "Physical Geography": ["erosion", "deposition", "river", "mountain", "plateau", "valley", "delta", "tectonic", "earthquake", "volcano"],
        "Human Geography": ["population density", "urbanisation", "migration", "settlement", "urban", "rural"],
        "Climate & Weather": ["climate", "rainfall", "temperature", "season", "monsoon", "wind", "humidity", "savanna", "rainforest", "desert"],
        "Cartography": ["latitude", "longitude", "map", "scale", "contour", "grid", "compass", "bearing"],
        "Environmental Geography": ["deforestation", "afforestation", "pollution", "conservation", "natural resource", "petroleum", "mineral"],
    },
    "Government & Political Science": {
        "Democracy & Government Types": ["democracy", "government", "republic", "monarchy", "autocracy", "dictatorship", "parliament", "senate"],
        "Electoral Systems": ["election", "voting", "referendum", "political party", "constituency", "proportional representation", "suffrage"],
        "Constitutional Law": ["constitution", "constitutional", "bill of rights", "separation of powers", "sovereignty", "federalism"],
        "International Relations": ["international relations", "diplomacy", "foreign policy", "united nations", "nato", "treaty"],
        "Governance": ["governance", "bureaucracy", "policy", "public administration", "corruption", "accountability", "citizenship"],
    },
    "English Language & Literature": {
        "Grammar & Syntax": ["grammar", "syntax", "noun", "pronoun", "verb", "adjective", "adverb", "preposition", "conjunction", "tense", "clause", "phrase"],
        "Literary Devices": ["metaphor", "simile", "alliteration", "irony", "symbolism", "figurative language", "personification", "hyperbole", "imagery"],
        "Poetry": ["poem", "poetry", "stanza", "verse", "rhyme", "rhythm", "sonnet", "lyric", "epic", "ode"],
        "Prose & Fiction": ["novel", "short story", "prose", "narrator", "plot", "character", "setting", "conflict", "theme"],
        "Essay Writing": ["essay", "paragraph", "introduction", "conclusion", "argument", "thesis", "cohesion"],
        "Comprehension & Vocabulary": ["comprehension", "vocabulary", "diction", "tone", "inference", "synonym", "antonym"],
    },
    "Sociology & Social Studies": {
        "Social Stratification": ["social class", "stratification", "inequality", "social mobility", "poverty", "wealth", "caste"],
        "Culture & Society": ["culture", "society", "norms", "values", "socialisation", "institution", "social structure", "tradition"],
        "Family & Community": ["family", "community", "kinship", "marriage", "household", "nuclear family", "extended family"],
        "Deviance & Crime": ["deviance", "crime", "social control", "punishment", "criminal justice", "recidivism"],
        "Social Change": ["social change", "globalisation", "modernisation", "urbanisation", "revolution"],
        "Gender & Identity": ["gender", "identity", "ethnicity", "race", "prejudice", "discrimination", "feminism"],
    },
    "Psychology": {
        "Cognitive Psychology": ["cognition", "memory", "perception", "attention", "thinking", "problem solving", "language", "intelligence", "iq"],
        "Developmental Psychology": ["development", "piaget", "vygotsky", "childhood", "adolescence", "stage theory", "attachment", "erikson"],
        "Social Psychology": ["social influence", "obedience", "conformity", "attitude", "persuasion", "group behaviour", "milgram", "bystander"],
        "Abnormal Psychology": ["mental health", "disorder", "depression", "anxiety", "schizophrenia", "therapy", "counselling", "dsm"],
        "Learning Theory": ["conditioning", "pavlov", "skinner", "reinforcement", "punishment", "stimulus", "response", "operant", "classical"],
        "Personality Theory": ["personality", "freud", "jung", "ego", "superego", "id", "trait theory", "big five"],
    },
    "Medicine & Health": {
        "Diseases & Diagnosis": ["disease", "diagnosis", "symptom", "syndrome", "infection", "pathology", "malaria", "hiv", "tuberculosis", "cancer", "diabetes"],
        "Pharmacology": ["drug", "medication", "dose", "pharmacology", "antibiotic", "vaccine", "side effect", "contraindication"],
        "Nutrition & Dietetics": ["nutrition", "diet", "vitamin", "mineral", "calorie", "protein", "carbohydrate", "fat", "obesity", "malnutrition"],
        "Public Health": ["public health", "epidemiology", "immunisation", "screening", "health promotion", "sanitation", "hygiene"],
        "First Aid & Emergency": ["first aid", "cpr", "resuscitation", "wound", "fracture", "emergency", "bleeding"],
    },
    "Engineering": {
        "Civil Engineering": ["civil engineering", "structural", "load", "beam", "column", "foundation", "concrete", "steel", "truss", "bridge"],
        "Mechanical Engineering": ["mechanical", "gear", "shaft", "turbine", "engine", "machine", "stress", "strain", "fluid flow"],
        "Electrical Engineering": ["electrical engineering", "circuit design", "signal", "digital logic", "amplifier", "transformer", "power system"],
        "Control Systems": ["control system", "feedback", "pid controller", "sensor", "actuator", "automation", "transfer function"],
        "Materials Science": ["material", "alloy", "tensile strength", "compressive", "welding", "hardness", "conductivity", "polymer"],
    },
    "Agriculture & Environmental Science": {
        "Crop Science": ["crop", "farming", "agriculture", "seed", "harvest", "irrigation", "fertiliser", "pesticide", "soil fertility"],
        "Livestock & Fisheries": ["livestock", "poultry", "fishery", "cattle", "breeding", "animal husbandry", "aquaculture"],
        "Soil Science": ["soil", "soil type", "erosion control", "land use", "compost", "humus", "mineral soil", "soil ph"],
        "Environmental Management": ["deforestation", "afforestation", "conservation", "biodiversity", "waste management", "recycling", "pollution"],
        "Climate Change": ["climate change", "global warming", "greenhouse gas", "carbon dioxide", "carbon footprint", "ozone"],
        "Renewable Energy": ["renewable energy", "solar", "wind energy", "hydropower", "biomass", "fossil fuel", "sustainability"],
    },
}


def extract_subtopic(question_text, broad_topic):
    """
    Given a question text and its already-classified broad topic, returns the most
    specific sub-topic name that matches, or None if no keyword is found.
    Scores longer/more specific keyword phrases higher to reward specificity.
    """
    text = f" {re.sub(r'[^a-z0-9]+', ' ', question_text.lower()).strip()} "

    subtopics = SUBTOPIC_MAP.get(broad_topic, {})
    best_subtopic = None
    best_score = 0

    for subtopic_name, keywords in subtopics.items():
        score = 0
        for kw in keywords:
            term = re.sub(r'[^a-z0-9]+', ' ', kw.lower()).strip()
            if f" {term} " in text:
                score += len(kw.split())  # longer phrases score higher
        if score > best_score:
            best_score = score
            best_subtopic = subtopic_name

    return best_subtopic  # None if nothing matched


def classify_topic_for_question(question_text, manual_topic="", use_ai=None):
    if manual_topic.strip():
        topic = manual_topic.strip()
        return topic, extract_subtopic(question_text, topic)
    if use_ai is None:
        use_ai = ai_available()
    if use_ai:
        try:
            return classify_question(question_text)
        except (AIUnavailable, ValueError):
            pass
    topic = auto_classify_topic(question_text)
    return topic, extract_subtopic(question_text, topic)


def student_search_analytics():
    st.header("Student Performance Review")

    query = st.text_input("Enter Student Matric Number:", placeholder="")
    if query and not query.isdigit():
        st.error("Invalid Matric Number. Please enter digits only.")
        return
    if not query:
        st.info("Enter a matric number to begin.")
        return

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM users WHERE staff_id LIKE %s AND role='student'", (f"%{query}%",))
    students = cursor.fetchall()

    if not students:
        st.warning("No student found.")
        conn.close()
        return

    if len(students) > 1:
        st.success(f"Found {len(students)} students.")
        student_options = {s['id']: f"{s['name']} - {s['staff_id']}" for s in students}
        selected_student_id = st.selectbox("Select Student", list(student_options.keys()), format_func=lambda x: student_options[x])
        target_student = next(s for s in students if s['id'] == selected_student_id)
    else:
        target_student = students[0]
        selected_student_id = target_student['id']

    with st.container(border=True):
        st.subheader("Student Profile")
        st.write("Name:", target_student["name"])
        st.write("Matric No:", target_student["staff_id"])
        st.write("Department:", target_student.get("department") or "N/A")

    cursor.execute("""
        SELECT r.id as result_id, r.score, r.total, r.date_taken, r.saved_answers, t.title, t.id as test_id
        FROM results r
        JOIN tests t ON r.test_id = t.id
        WHERE r.user_id = %s AND r.status = 'completed'
        ORDER BY r.date_taken ASC
    """, (selected_student_id,))

    results = cursor.fetchall()

    if not results:
        st.info("This student has not completed any tests yet.")
        conn.close()
        return

    tab1, tab2 = st.tabs(["Overall Learning Analytics", "Specific Test Review"])

    with tab1:
        st.subheader("Performance Over Time")
        df_results = pd.DataFrame(results)
        df_results["Percentage"] = (df_results["score"] / df_results["total"] * 100).round(1)

        fig_line = px.line(df_results, x="date_taken", y="Percentage", text="Percentage",
                           markers=True, title="Score Progression",
                           labels={"date_taken": "Date", "Percentage": "Score (%)"})
        fig_line.update_traces(textposition="top center", marker=dict(size=14, color="#00b4d8", line=dict(width=2, color="white")), line=dict(width=4), cliponaxis=False)
        fig_line.update_yaxes(range=[0, 100])
        st.plotly_chart(fig_line, use_container_width=True)
        st.markdown("---")
        st.subheader("Topic Strengths & Weaknesses")

        topic_data = []
        for r in results:
            cursor.execute("SELECT id, topic, correct_option FROM questions WHERE test_id=%s", (r['test_id'],))
            questions = cursor.fetchall()
            saved_ans = json.loads(r['saved_answers']) if r['saved_answers'] else {}
            for q in questions:
                student_ans = saved_ans.get(str(q['id']), None)
                is_correct = 1 if student_ans == q['correct_option'] else 0
                raw_topic = q['topic'] if q['topic'] else "General"
                # Normalize: treat "General" as "General Knowledge"
                normalized_topic = "General Knowledge" if raw_topic.strip().lower() == "general" else raw_topic
                topic_data.append({"Test": r['title'], "Topic": normalized_topic, "Correct": is_correct, "Attempted": 1})

        if topic_data:
            df_topics = pd.DataFrame(topic_data)
            topic_summary = df_topics.groupby("Topic", as_index=False).agg({"Correct": "sum", "Attempted": "sum"})
            topic_summary["Accuracy (%)"] = (topic_summary["Correct"] / topic_summary["Attempted"] * 100).round(1)

            col_a, col_b = st.columns(2)
            with col_a:
                fig_bar = px.bar(topic_summary, x="Topic", y="Accuracy (%)", color="Accuracy (%)", color_continuous_scale="RdYlGn", range_color=[0, 100], title="Accuracy by Topic")
                fig_bar.update_yaxes(range=[0, 100])
                st.plotly_chart(fig_bar, use_container_width=True)
            with col_b:
                fig_pie = px.pie(topic_summary, values="Attempted", names="Topic", title="Volume of Questions Attempted per Topic", hole=0.4)
                st.plotly_chart(fig_pie, use_container_width=True)

            st.subheader("Topic Performance Heatmap Across Tests")

            # Dropdown to select a specific topic
            all_topics = sorted(df_topics["Topic"].unique().tolist())
            selected_topic = st.selectbox("Select a Topic to view:", ["All Topics"] + all_topics, key="admin_heatmap_topic")

            if selected_topic == "All Topics":
                filtered_topics = df_topics
            else:
                filtered_topics = df_topics[df_topics["Topic"] == selected_topic]

            heatmap_data = filtered_topics.groupby(["Test", "Topic"])["Correct"].mean().reset_index()
            heatmap_data["Correct"] = heatmap_data["Correct"] * 100
            # Pivot so missing topic/test combos become NaN (blank) instead of 0 (red)
            heatmap_pivot = heatmap_data.pivot(index="Test", columns="Topic", values="Correct")
            fig_heat = px.imshow(
                heatmap_pivot,
                color_continuous_scale="RdYlGn",
                zmin=0, zmax=100,
                labels={"color": "Accuracy (%)"},
                title=f"Accuracy Heatmap — {selected_topic} (Green = Strong, Red = Weak)",
                aspect="auto"
            )
            fig_heat.update_xaxes(title="Topic")
            fig_heat.update_yaxes(title="Test")
            st.plotly_chart(fig_heat, use_container_width=True)

    with tab2:
        st.subheader(f"Test History for {target_student['name']}")
        sorted_results = sorted(results, key=lambda x: x['date_taken'], reverse=True)
        result_options = {r['result_id']: f"{r['title']} | Score: {r['score']}/{r['total']} | {r['date_taken']}" for r in sorted_results}
        selected_result_id = st.selectbox("Select a Test to Analyze:", list(result_options.keys()), format_func=lambda x: result_options[x])
        target_result = next(r for r in results if r['result_id'] == selected_result_id)

        c1, c2, c3 = st.columns(3)
        c1.metric("Score", f"{target_result['score']} / {target_result['total']}")
        percentage = (target_result['score'] / target_result['total'] * 100) if target_result['total'] > 0 else 0
        c2.metric("Percentage", f"{percentage:.1f}%")
        c3.metric("Date Taken", str(target_result['date_taken'].date()))

        st.markdown("---")
        st.subheader("Integrity & Malpractice Audit")
        cursor.execute("SELECT timestamp, description FROM malpractice_logs WHERE user_id=%s AND test_id=%s ORDER BY timestamp DESC", (selected_student_id, target_result['test_id']))
        logs = cursor.fetchall()

        if logs:
            st.warning(f"{len(logs)} exam integrity event(s) recorded; review the details before drawing conclusions.")
            st.dataframe(pd.DataFrame(logs), use_container_width=True, hide_index=True)
        else:
            st.info("No tab-switch events were recorded. Browser detection is best-effort.")

        st.subheader("Answer Script Breakdown")
        cursor.execute("SELECT * FROM questions WHERE test_id = %s", (target_result['test_id'],))
        questions = cursor.fetchall()
        student_answers = json.loads(target_result['saved_answers']) if target_result['saved_answers'] else {}

        analysis_data = []
        for idx, q in enumerate(questions):
            s_ans = student_answers.get(str(q['id']), "Skipped")
            is_correct = (s_ans == q['correct_option'])
            analysis_data.append({
                "No.": idx + 1,
                "Question": q['question'],
                "Student Answer": s_ans,
                "Correct Answer": q['correct_option'],
                "Status": "Correct" if is_correct else "Wrong"
            })

        df_analysis = pd.DataFrame(analysis_data)

        def color_status(val):
            if not isinstance(val, str): return ''
            bg_color = 'rgba(40, 167, 69, 0.15)' if val == "Correct" else 'rgba(220, 53, 69, 0.15)'
            text_color = '#4ade80' if val == "Correct" else '#f87171'
            return f'background-color: {bg_color}; color: {text_color}; font-weight: bold;'

        st.dataframe(df_analysis.style.map(color_status, subset=['Status']), use_container_width=True, hide_index=True)

    conn.close()

def create_test():
    st.header("Create New Test")
    with st.form("create_test_form"):
        title = st.text_input("Test Title")
        c1, c2 = st.columns(2)
        hours = c1.number_input("Hours", 0, 5, 0)
        minutes = c2.number_input("Minutes", 0, 180, 30)
        max_students = st.number_input("Max Students (0 = unlimited)", 0, value=0)
        release = st.selectbox("Release Option", ["immediate", "after_limit", "do_not_release"])
        if release == "after_limit":
            st.caption("Scores are released once the set number of distinct students have completed this test.")

        c3, c4 = st.columns(2)
        show_correct = c3.checkbox("Show Correct Answers?")
        visible = c4.checkbox("Visible to Students?", value=True)

        if st.form_submit_button("Create Test"):
            duration = hours * 60 + minutes
            if not title.strip():
                st.error("Title is required.")
            elif duration < 1:
                st.error("Duration must be at least one minute.")
            elif release == "after_limit" and max_students < 1:
                st.error("Set a student limit above zero to release scores after that limit is reached.")
            else:
                conn = get_db_connection()
                cur = conn.cursor()
                cur.execute("""
                    INSERT INTO tests (title, duration, created_by, max_students, release_option, show_correct_answers, visible)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                """, (title, duration, st.session_state["user"]["id"], max_students, release, show_correct, visible))
                conn.commit()
                conn.close()
                st.success(f"Test '{title}' created!")

def process_smart_paste(raw_text, test_id, manual_topic, use_ai=None):
    if len(raw_text) > 30000:
        return 0, ["Paste at most 30,000 characters at a time."]
    blocks = re.split(r'\n\s*\n', raw_text.strip())
    success_count = 0
    error_messages = []
    parsed = []

    for index, block in enumerate(blocks):
        block = block.strip()
        if not block:
            continue

        try:
            lines = [line.strip() for line in block.split('\n') if line.strip()]

            if len(lines) < 2:
                raise ValueError("Question block is too short. Missing options or ANSWER line.")

            answer_line = lines[-1]
            answer_match = re.fullmatch(r'(?:ANSWER|CORRECT ANSWER)\s*[:\-]\s*([A-D])\s*', answer_line, re.I)
            if not answer_match:
                raise ValueError("The last line must give an explicit answer, such as 'ANSWER: B'.")
            correct_option = answer_match.group(1).upper()

            options_dict = {}
            question_lines = []

            for line in lines[:-1]:
                match = re.match(r'^([A-D])\s*[\)\.\:\-]\s*(.+)$', line, re.I)
                if match:
                    option_letter = match.group(1).upper()
                    option_text = match.group(2).strip()
                    if option_letter in options_dict:
                        raise ValueError(f"Option {option_letter} appears more than once.")
                    options_dict[option_letter] = option_text
                else:
                    question_lines.append(line)

            question_text = "\n".join(question_lines).strip()

            if not question_text or set(options_dict) != set("ABCD"):
                raise ValueError("Include a question and exactly one non-empty option for each of A, B, C and D.")
            parsed.append((question_text, options_dict, correct_option))

        except ValueError as exc:
            error_messages.append(f"Question {index + 1}: {exc}")

    if error_messages:
        return 0, error_messages
    if not parsed:
        return 0, error_messages or ["No valid questions were found."]

    if use_ai is None:
        use_ai = ai_available()
    ai_labels = []
    if use_ai and not manual_topic.strip():
        batch = []
        batch_length = 0
        for question, _, _ in parsed[:20]:
            if len(question) > 3000 or batch_length + len(question) > 12000:
                break
            batch.append(question)
            batch_length += len(question)
        try:
            if batch:
                ai_labels = classify_questions(batch)
        except (AIUnavailable, ValueError):
            pass

    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        for index, (question_text, options, correct_option) in enumerate(parsed):
            topic, subtopic = (
                ai_labels[index] if index < len(ai_labels)
                else classify_topic_for_question(question_text, manual_topic, use_ai=False)
            )
            cursor.execute("""
                INSERT INTO questions (test_id, question, option_a, option_b, option_c, option_d, correct_option, topic, subtopic)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (test_id, question_text, *(options[label] for label in "ABCD"), correct_option, topic, subtopic))
            success_count += 1
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return success_count, error_messages

def add_questions():
    st.header("Add Questions to Test Bank")
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute("SELECT id, title FROM tests")
    tests = cursor.fetchall()

    if not tests:
        st.warning("Please create a test first before adding questions.")
        conn.close()
        return

    test_options = {t['id']: t['title'] for t in tests}
    selected_test_id = st.selectbox("Select Test", list(test_options.keys()), format_func=lambda x: test_options[x])

    topic = st.text_input("Topic / Subject Area (optional manual override)")
    st.caption("With a configured AI key, question formatting and topic tagging use AI first. The local parser and classifier take over if AI is unavailable. Question text may be sent to Google or Groq when AI runs.")

    tab1, tab2 = st.tabs(["Smart Paste (Bulk Text)", "Manual Single Entry"])

    with tab1:
        st.info("Paste questions from a document. Explicitly mark each correct answer; AI will arrange the text for review when available. Already-formatted questions are processed without an AI call. Nothing is saved until you confirm the draft.")

        with st.expander("See formatting example"):
            st.code("""What does HTML stand for?
A. Hyper Text Markup Language
B. Home Tool Markup Language
C. Hyperlinks and Text Markup Language
D. Hyper Tool Multi Language
ANSWER: A""")

        raw_text = st.text_area("Paste your examination text here:", height=300)

        if st.button("Prepare questions"):
            if not raw_text.strip():
                st.warning("Paste some questions first.")
            else:
                draft = raw_text
                if ai_available():
                    try:
                        draft = format_questions(raw_text)
                    except ValueError as exc:
                        st.warning(str(exc))
                    except AIUnavailable:
                        st.info("AI formatting is unavailable or could not verify every answer; review and correct the draft below.")
                st.session_state["smart_paste_source"] = hashlib.sha256(raw_text.encode()).hexdigest()
                st.session_state["smart_paste_draft"] = draft

        current_source = hashlib.sha256(raw_text.encode()).hexdigest()
        if st.session_state.get("smart_paste_source") == current_source:
            st.caption("Check every question, option and marked answer. Correct the draft before saving.")
            text_to_save = st.text_area("Prepared questions for review", key="smart_paste_draft", height=300)
            save = st.button("Save reviewed questions", type="primary")
        else:
            text_to_save = ""
            save = False

        if save:
            if text_to_save.strip():
                with st.spinner("Validating and saving questions..."):
                    successes, errors = process_smart_paste(text_to_save, selected_test_id, topic)

                if successes > 0:
                    st.success(f"Successfully extracted and saved {successes} questions to the database!")

                if errors:
                    st.error("No questions were saved. Correct the issues below and try again:")
                    for error in errors:
                        st.warning(error)
            else:
                st.warning("The reviewed draft is empty.")

    with tab2:
        question = st.text_area("Question Text")
        uploaded_image = st.file_uploader(
            "Upload Diagram/Image (Optional)", type=["png", "jpg", "jpeg", "gif", "webp", "bmp", "tif", "tiff"]
        )

        col1, col2 = st.columns(2)
        with col1:
            opt_a = st.text_input("Option A")
            opt_c = st.text_input("Option C")
        with col2:
            opt_b = st.text_input("Option B")
            opt_d = st.text_input("Option D")

        correct_opt = st.selectbox("Correct Option", ["A", "B", "C", "D"])

        if st.button("Add Single Question"):
            if question and opt_a and opt_b and opt_c and opt_d:
                image_path = None
                image_bytes = None
                if uploaded_image is not None:
                    try:
                        image_bytes, suffix = prepare_question_image(
                            uploaded_image.getvalue(), uploaded_image.name
                        )
                    except ValueError as exc:
                        st.error(str(exc))
                        conn.close()
                        return
                final_topic, final_subtopic = classify_topic_for_question(question, topic)
                try:
                    if image_bytes is not None:
                        upload_dir = os.path.join(os.path.dirname(__file__), "uploads")
                        os.makedirs(upload_dir, exist_ok=True)
                        image_path = os.path.join(upload_dir, f"{uuid.uuid4().hex}{suffix}")
                        with open(image_path, "wb") as image_file:
                            image_file.write(image_bytes)
                    cursor.execute("""
                        INSERT INTO questions (test_id, question, option_a, option_b, option_c, option_d, correct_option, topic, subtopic, image_path)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """, (selected_test_id, question, opt_a, opt_b, opt_c, opt_d, correct_opt, final_topic, final_subtopic, image_path))
                    conn.commit()
                except (OSError, sqlite3.Error):
                    conn.rollback()
                    if image_path and os.path.isfile(image_path):
                        os.remove(image_path)
                    st.error("The question or its image could not be saved. Please try again.")
                    conn.close()
                    return
                st.success(f"Question added successfully! Tagged under: **{final_topic}**")
            else:
                st.error("Please fill all text fields.")

    conn.close()

def view_tests():
    st.header("All Tests")
    conn = get_db_connection()
    df = pd.read_sql("""
        SELECT t.id, t.title, t.duration, u.name as created_by, t.created_at,
               t.max_students, t.release_option, t.visible, t.show_correct_answers
        FROM tests t
        LEFT JOIN users u ON t.created_by = u.id
    """, conn)
    conn.close()

    if not df.empty:
        def format_duration(minutes):
            h = minutes // 60
            m = minutes % 60
            return f"{h} hr {m} min" if h > 0 else f"{m} min"

        df['duration'] = df['duration'].apply(format_duration)

    st.dataframe(df, use_container_width=True, hide_index=True)

def completed_test_results(conn, test_id):
    """Exclude attempts that have not yet been submitted from class statistics."""
    return pd.read_sql("""
        SELECT users.name, users.staff_id as matric, results.score, results.total
        FROM results
        JOIN users ON results.user_id = users.id
        WHERE results.test_id = ? AND results.status = 'completed'
    """, conn, params=(int(test_id),))


def test_analytics():
    st.header("Overall Analytics")
    conn = get_db_connection()
    tests = pd.read_sql("SELECT * FROM tests", conn)

    if tests.empty:
        conn.close()
        return

    selected_test_id = st.selectbox("Select Test", tests["id"], format_func=lambda x: tests[tests["id"]==x]["title"].values[0])

    results = completed_test_results(conn, selected_test_id)
    conn.close()

    if results.empty:
        st.info("No results yet.")
        return

    results["Percentage"] = (results["score"] / results["total"].replace(0, np.nan) * 100).fillna(0)

    c1, c2, c3 = st.columns(3)
    c1.metric("Students", len(results))
    c2.metric("Avg Score", f"{results['Percentage'].mean():.1f}%")
    c3.metric("Pass Rate", f"{len(results[results['Percentage']>=50])/len(results)*100:.0f}%")

    average = results["Percentage"].mean()
    pass_rate = len(results[results["Percentage"] >= 50]) / len(results) * 100
    fallback_insight = f"Across {len(results)} completed results, the average score is {average:.1f}% and {pass_rate:.1f}% scored at least 50%."
    insight_key = f"class_insight_{selected_test_id}"
    insight_signature = (len(results), round(average, 1), round(pass_rate, 1))
    if ai_available() and st.session_state.get(insight_key, (None,))[0] != insight_signature:
        try:
            insight = summarise_class_results(*insight_signature)
        except (AIUnavailable, ValueError):
            insight = None
        st.session_state[insight_key] = (insight_signature, insight)
    class_insight = st.session_state.get(insight_key, (None, None))[1] if ai_available() else None
    st.caption("AI-generated class insight; check it against the figures above." if class_insight else "Class summary from recorded scores.")
    st.write(class_insight or fallback_insight)

    st.dataframe(results, hide_index=True, use_container_width=True)

    if st.button("Generate PDF Report"):
        pdf = FPDF()
        pdf.add_page()
        pdf.set_font("Arial", "B", 16)
        pdf.cell(0, 10, f"Results: {tests[tests['id']==selected_test_id]['title'].values[0]}", ln=True, align='C')
        pdf.set_font("Arial", size=12)
        pdf.ln(10)

        pdf.cell(40, 10, "Matric", 1)
        pdf.cell(80, 10, "Name", 1)
        pdf.cell(30, 10, "Score", 1)
        pdf.cell(30, 10, "%", 1)
        pdf.ln()

        for _, row in results.iterrows():
            pdf.cell(40, 10, str(row['matric']), 1)
            pdf.cell(80, 10, str(row['name']), 1)
            pdf.cell(30, 10, f"{row['score']}/{row['total']}", 1)
            pdf.cell(30, 10, f"{row['Percentage']:.1f}", 1)
            pdf.ln()

        document = pdf.output(dest="S")
        if isinstance(document, str):
            document = document.encode("latin-1")
        st.download_button("Download PDF", document, "result_report.pdf", "application/pdf")
